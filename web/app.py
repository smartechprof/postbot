"""
PostBot Web — Flask application for OAuth flows and dashboard.
Reads credentials from /etc/igbot.env (same as PostBot CLI).
"""
import os
import json
import fcntl
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from the same file as PostBot
ENV_PATH = os.getenv("ENV_FILE", "/etc/igbot.env")
if Path(ENV_PATH).exists():
    load_dotenv(ENV_PATH)

import firebase_admin
import firebase_admin.auth
import firebase_admin.credentials
from flask import Flask, jsonify, render_template, redirect, request, send_from_directory, session, url_for

log = logging.getLogger("postbot-web")
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(32).hex())

# ── Firebase Admin ──────────────────────────────────────────────────────────
_SA_PATH = os.getenv(
    "FIREBASE_SERVICE_ACCOUNT_PATH",
    os.path.expanduser("~/.secrets/firebase-service-account.json"),
)
try:
    firebase_admin.initialize_app(firebase_admin.credentials.Certificate(_SA_PATH))
    log.info("Firebase Admin initialized from %s", _SA_PATH)
except Exception as exc:
    log.warning("Firebase Admin init failed — /auth/verify will be unavailable: %s", exc)


# ── User data storage ──────────────────────────────────────────────────────
USER_DATA_PATH = os.getenv("USER_DATA_PATH", "user_data.json")


def get_connected_platforms(uid: str) -> list:
    path = Path(USER_DATA_PATH)
    if not path.exists():
        return []
    try:
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return data.get(uid, [])
    except Exception as exc:
        log.warning("get_connected_platforms failed: %s", exc)
        return []


def save_connected_platform(uid: str, platform: str) -> None:
    path = Path(USER_DATA_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                content = f.read()
                data = json.loads(content) if content.strip() else {}
                platforms = data.get(uid, [])
                if platform not in platforms:
                    platforms.append(platform)
                data[uid] = platforms
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as exc:
        log.warning("save_connected_platform failed: %s", exc)


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    return send_from_directory(app.static_folder, "postbot_icon.png")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login.html")
def login():
    if session.get("user"):
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    if not session.get("user"):
        return redirect(url_for("login"))
    uid = session["user"]["uid"]
    connected = get_connected_platforms(uid)
    session["connected_platforms"] = connected
    return render_template(
        "dashboard.html",
        tiktok_client_key=os.getenv("TIKTOK_CLIENT_KEY", ""),
        youtube_client_id=os.getenv("YT_WEB_CLIENT_ID", ""),
        connected_platforms=connected,
    )


@app.route("/publish")
def publish():
    if not session.get("user"):
        return redirect(url_for("login"))
    return render_template("publish.html")


@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")
    if code and state and not error:
        uid = (session.get("user") or {}).get("uid")
        if uid:
            save_connected_platform(uid, state)
        connected = session.get("connected_platforms", [])
        if state not in connected:
            connected.append(state)
        session["connected_platforms"] = connected
    return render_template("oauth_callback.html", code=code, state=state, error=error)


@app.route("/auth/verify", methods=["POST"])
def auth_verify():
    data = request.get_json(silent=True) or {}
    id_token = data.get("token", "").strip()
    if not id_token:
        return jsonify({"ok": False, "error": "Missing token."}), 401
    try:
        decoded = firebase_admin.auth.verify_id_token(id_token)
    except Exception as exc:
        log.warning("Token verification failed: %s", exc)
        return jsonify({"ok": False, "error": "Invalid or expired token."}), 401
    session["user"] = {
        "uid":   decoded.get("uid"),
        "email": decoded.get("email", ""),
        "name":  decoded.get("name", ""),
    }
    log.info("User signed in: %s", session["user"]["email"])
    return jsonify({"ok": True})


@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/tos.html")
def tos():
    return render_template("tos.html")


@app.route("/privacy.html")
def privacy():
    return render_template("privacy.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
