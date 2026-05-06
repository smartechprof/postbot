"""
PostBot Web — Flask application for OAuth flows and dashboard.
Reads credentials from /etc/igbot.env (same as PostBot CLI).
"""
import os
import hashlib
import base64
import secrets
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
import requests as http_requests_mod

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


def _read_data() -> dict:
    path = Path(USER_DATA_PATH)
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as exc:
        log.warning("_read_data failed: %s", exc)
        return {}


def _user_record(data: dict, uid: str) -> dict:
    record = data.get(uid, {})
    if isinstance(record, list):
        return {"platforms": record}
    return record


def get_connected_platforms(uid: str) -> list:
    return _user_record(_read_data(), uid).get("platforms", [])


def get_drive_folder(uid: str) -> str:
    return _user_record(_read_data(), uid).get("drive_folder", "")


def remove_connected_platform(uid: str, platform: str) -> None:
    path = Path(USER_DATA_PATH)
    if not path.exists():
        return
    try:
        with open(path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                content = f.read()
                data = json.loads(content) if content.strip() else {}
                record = _user_record(data, uid)
                platforms = record.get("platforms", [])
                if platform in platforms:
                    platforms.remove(platform)
                record["platforms"] = platforms
                data[uid] = record
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as exc:
        log.warning("remove_connected_platform failed: %s", exc)


def _save_user_field(uid: str, key: str, value: str) -> None:
    """Save a single field to the user record in user_data.json."""
    path = Path(USER_DATA_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                content = f.read()
                data = json.loads(content) if content.strip() else {}
                record = _user_record(data, uid)
                record[key] = value
                data[uid] = record
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as exc:
        log.warning("_save_user_field(%s) failed: %s", key, exc)


def save_drive_folder(uid: str, folder_name: str) -> None:
    _save_user_field(uid, "drive_folder", folder_name)


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
                record = _user_record(data, uid)
                platforms = record.get("platforms", [])
                if platform not in platforms:
                    platforms.append(platform)
                record["platforms"] = platforms
                data[uid] = record
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as exc:
        log.warning("save_connected_platform failed: %s", exc)


def fetch_youtube_channel_name(code: str) -> str:
    """Exchange OAuth code for token and fetch YouTube channel name."""
    try:
        token_resp = http_requests_mod.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": os.getenv("YT_WEB_CLIENT_ID", ""),
                "client_secret": os.getenv("YT_WEB_CLIENT_SECRET", ""),
                "redirect_uri": "https://botshub.io/oauth/callback",
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        if not token_resp.ok:
            log.warning("YouTube token exchange failed: %s", token_resp.text[:200])
            return ""
        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            return ""
        ch_resp = http_requests_mod.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if not ch_resp.ok:
            log.warning("YouTube channels.list failed: %s", ch_resp.text[:200])
            return ""
        items = ch_resp.json().get("items", [])
        if items:
            return items[0].get("snippet", {}).get("title", "")
        return ""
    except Exception as exc:
        log.warning("fetch_youtube_channel_name error: %s", exc)
        return ""


def save_youtube_channel_name(uid: str, channel_name: str) -> None:
    _save_user_field(uid, "youtube_channel_name", channel_name)


def get_youtube_channel_name(uid: str) -> str:
    return _user_record(_read_data(), uid).get("youtube_channel_name", "")


def fetch_tiktok_username(code: str) -> str:
    """Exchange OAuth code for token and fetch TikTok username."""
    try:
        token_resp = http_requests_mod.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": os.getenv("TIKTOK_CLIENT_KEY", ""),
                "client_secret": os.getenv("TIKTOK_CLIENT_SECRET", ""),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": "https://botshub.io/oauth/callback",
            },
            timeout=15,
        )
        if not token_resp.ok:
            log.warning("TikTok token exchange failed: %s", token_resp.text[:200])
            return ""
        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            return ""
        info_resp = http_requests_mod.get(
            "https://open.tiktokapis.com/v2/user/info/",
            params={"fields": "display_name"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if not info_resp.ok:
            log.warning("TikTok user.info failed: %s", info_resp.text[:200])
            return ""
        user_data = info_resp.json().get("data", {}).get("user", {})
        return user_data.get("display_name", "") or user_data.get("username", "")
    except Exception as exc:
        log.warning("fetch_tiktok_username error: %s", exc)
        return ""


def save_tiktok_username(uid: str, username: str) -> None:
    _save_user_field(uid, "tiktok_username", username)


def get_tiktok_username(uid: str) -> str:
    return _user_record(_read_data(), uid).get("tiktok_username", "")


def save_bluesky_handle(uid: str, handle: str) -> None:
    _save_user_field(uid, "bluesky_handle", handle)


def get_bluesky_handle(uid: str) -> str:
    return _user_record(_read_data(), uid).get("bluesky_handle", "")


# ── Instagram ──────────────────────────────────────────────────────────────

def fetch_instagram_username(code: str) -> str:
    """Exchange OAuth code for token and fetch Instagram username."""
    try:
        token_resp = http_requests_mod.post(
            "https://graph.facebook.com/v19.0/oauth/access_token",
            data={
                "client_id": os.getenv("META_APP_ID", ""),
                "client_secret": os.getenv("META_APP_SECRET", ""),
                "code": code,
                "redirect_uri": "https://botshub.io/oauth/callback",
            },
            timeout=15,
        )
        if not token_resp.ok:
            log.warning("Instagram token exchange failed: %s", token_resp.text[:200])
            return ""
        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            return ""
        user_resp = http_requests_mod.get(
            "https://graph.instagram.com/me",
            params={"fields": "username"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if not user_resp.ok:
            log.warning("Instagram user info failed: %s", user_resp.text[:200])
            return ""
        return user_resp.json().get("username", "")
    except Exception as exc:
        log.warning("fetch_instagram_username error: %s", exc)
        return ""


def save_instagram_username(uid: str, username: str) -> None:
    _save_user_field(uid, "instagram_username", username)


def get_instagram_username(uid: str) -> str:
    return _user_record(_read_data(), uid).get("instagram_username", "")


# ── Facebook ───────────────────────────────────────────────────────────────

def fetch_facebook_name(code: str) -> str:
    """Exchange OAuth code for token and fetch Facebook user/page name."""
    try:
        token_resp = http_requests_mod.post(
            "https://graph.facebook.com/v19.0/oauth/access_token",
            data={
                "client_id": os.getenv("META_APP_ID", ""),
                "client_secret": os.getenv("META_APP_SECRET", ""),
                "code": code,
                "redirect_uri": "https://botshub.io/oauth/callback",
            },
            timeout=15,
        )
        if not token_resp.ok:
            log.warning("Facebook token exchange failed: %s", token_resp.text[:200])
            return ""
        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            return ""
        user_resp = http_requests_mod.get(
            "https://graph.facebook.com/me",
            params={"fields": "name"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if not user_resp.ok:
            log.warning("Facebook user info failed: %s", user_resp.text[:200])
            return ""
        return user_resp.json().get("name", "")
    except Exception as exc:
        log.warning("fetch_facebook_name error: %s", exc)
        return ""


def save_facebook_name(uid: str, name: str) -> None:
    _save_user_field(uid, "facebook_name", name)


def get_facebook_name(uid: str) -> str:
    return _user_record(_read_data(), uid).get("facebook_name", "")


# ── Threads ────────────────────────────────────────────────────────────────

def fetch_threads_username(code: str) -> str:
    """Exchange OAuth code for token and fetch Threads username."""
    try:
        token_resp = http_requests_mod.post(
            "https://graph.threads.net/oauth/access_token",
            data={
                "client_id": os.getenv("META_APP_ID", ""),
                "client_secret": os.getenv("META_APP_SECRET", ""),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": "https://botshub.io/oauth/callback",
            },
            timeout=15,
        )
        if not token_resp.ok:
            log.warning("Threads token exchange failed: %s", token_resp.text[:200])
            return ""
        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            return ""
        user_resp = http_requests_mod.get(
            "https://graph.threads.net/v1.0/me",
            params={"fields": "username"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if not user_resp.ok:
            log.warning("Threads user info failed: %s", user_resp.text[:200])
            return ""
        return user_resp.json().get("username", "")
    except Exception as exc:
        log.warning("fetch_threads_username error: %s", exc)
        return ""


def save_threads_username(uid: str, username: str) -> None:
    _save_user_field(uid, "threads_username", username)


def get_threads_username(uid: str) -> str:
    return _user_record(_read_data(), uid).get("threads_username", "")


# ── LinkedIn ───────────────────────────────────────────────────────────────

def fetch_linkedin_name(code: str) -> str:
    """Exchange OAuth code for token and fetch LinkedIn user name."""
    try:
        token_resp = http_requests_mod.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "client_id": os.getenv("LI_CLIENT_ID", ""),
                "client_secret": os.getenv("LI_CLIENT_SECRET", ""),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": "https://botshub.io/oauth/callback",
            },
            timeout=15,
        )
        if not token_resp.ok:
            log.warning("LinkedIn token exchange failed: %s", token_resp.text[:200])
            return ""
        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            return ""
        user_resp = http_requests_mod.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if not user_resp.ok:
            log.warning("LinkedIn user info failed: %s", user_resp.text[:200])
            return ""
        return user_resp.json().get("name", "")
    except Exception as exc:
        log.warning("fetch_linkedin_name error: %s", exc)
        return ""


def save_linkedin_name(uid: str, name: str) -> None:
    _save_user_field(uid, "linkedin_name", name)


def get_linkedin_name(uid: str) -> str:
    return _user_record(_read_data(), uid).get("linkedin_name", "")


# ── Google Business Profile ───────────────────────────────────────────────

def fetch_gbp_account_name(code: str) -> str:
    """Exchange OAuth code for token and fetch GBP business name."""
    try:
        token_resp = http_requests_mod.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": os.getenv("YT_WEB_CLIENT_ID", ""),
                "client_secret": os.getenv("YT_WEB_CLIENT_SECRET", ""),
                "redirect_uri": "https://botshub.io/oauth/callback",
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        if not token_resp.ok:
            log.warning("GBP token exchange failed: %s", token_resp.text[:200])
            return ""
        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            return ""
        acct_resp = http_requests_mod.get(
            "https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if not acct_resp.ok:
            log.warning("GBP accounts.list failed: %s", acct_resp.text[:200])
            return ""
        accounts = acct_resp.json().get("accounts", [])
        if accounts:
            return accounts[0].get("accountName", "")
        return ""
    except Exception as exc:
        log.warning("fetch_gbp_account_name error: %s", exc)
        return ""


def save_gbp_account_name(uid: str, name: str) -> None:
    _save_user_field(uid, "gbp_account_name", name)


def get_gbp_account_name(uid: str) -> str:
    return _user_record(_read_data(), uid).get("gbp_account_name", "")


# ── X (Twitter) ────────────────────────────────────────────────────────────

def fetch_twitter_username(code: str, code_verifier: str) -> str:
    """Exchange OAuth code + PKCE verifier for token and fetch X username."""
    try:
        token_resp = http_requests_mod.post(
            "https://api.x.com/2/oauth2/token",
            data={
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": "https://botshub.io/oauth/callback",
                "code_verifier": code_verifier,
                "client_id": os.getenv("X_CLIENT_ID", ""),
            },
            auth=(os.getenv("X_CLIENT_ID", ""), os.getenv("X_CLIENT_SECRET", "")),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if not token_resp.ok:
            log.warning("X token exchange failed: %s", token_resp.text[:200])
            return ""
        access_token = token_resp.json().get("access_token", "")
        if not access_token:
            return ""
        user_resp = http_requests_mod.get(
            "https://api.x.com/2/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if not user_resp.ok:
            log.warning("X user info failed: %s", user_resp.text[:200])
            return ""
        return user_resp.json().get("data", {}).get("username", "")
    except Exception as exc:
        log.warning("fetch_twitter_username error: %s", exc)
        return ""


def save_twitter_username(uid: str, username: str) -> None:
    _save_user_field(uid, "twitter_username", username)


def get_twitter_username(uid: str) -> str:
    return _user_record(_read_data(), uid).get("twitter_username", "")


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    return send_from_directory(os.path.join(app.static_folder, "icons"), "postbot_icon.png")


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
        google_drive_client_id=os.getenv("YT_WEB_CLIENT_ID", ""),
        connected_platforms=connected,
        drive_folder=get_drive_folder(uid),
        youtube_channel_name=get_youtube_channel_name(uid),
        tiktok_username=get_tiktok_username(uid),
        bluesky_handle=get_bluesky_handle(uid),
        instagram_username=get_instagram_username(uid),
        facebook_name=get_facebook_name(uid),
        threads_username=get_threads_username(uid),
        linkedin_name=get_linkedin_name(uid),
        gbp_account_name=get_gbp_account_name(uid),
        twitter_username=get_twitter_username(uid),
        meta_app_id=os.getenv("META_APP_ID", ""),
        li_client_id=os.getenv("LI_CLIENT_ID", ""),
    )


@app.route("/publish")
def publish():
    if not session.get("user"):
        return redirect(url_for("login"))
    uid = session["user"]["uid"]
    connected = get_connected_platforms(uid)
    return render_template(
        "publish.html",
        connected_platforms=connected,
        drive_folder=get_drive_folder(uid),
        youtube_channel_name=get_youtube_channel_name(uid),
        tiktok_username=get_tiktok_username(uid),
        bluesky_handle=get_bluesky_handle(uid),
        instagram_username=get_instagram_username(uid),
        facebook_name=get_facebook_name(uid),
        threads_username=get_threads_username(uid),
        linkedin_name=get_linkedin_name(uid),
        gbp_account_name=get_gbp_account_name(uid),
        twitter_username=get_twitter_username(uid),
    )



@app.route("/oauth/x/start")
def oauth_x_start():
    if not session.get("user"):
        return redirect(url_for("login"))
    code_verifier = secrets.token_urlsafe(64)
    session["x_code_verifier"] = code_verifier
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    client_id = os.getenv("X_CLIENT_ID", "")
    auth_url = (
        "https://x.com/i/oauth2/authorize"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri=https%3A%2F%2Fbotshub.io%2Foauth%2Fcallback"
        f"&scope=tweet.read%20tweet.write%20users.read%20offline.access"
        f"&state=twitter"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )
    return redirect(auth_url)

@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")
    if code and state and not error:
        uid = (session.get("user") or {}).get("uid")
        if uid:
            save_connected_platform(uid, state)
            if state == "youtube":
                channel_name = fetch_youtube_channel_name(code)
                if channel_name:
                    save_youtube_channel_name(uid, channel_name)
                    log.info("YouTube channel name saved: %s", channel_name)
            if state == "tiktok":
                tiktok_user = fetch_tiktok_username(code)
                if tiktok_user:
                    save_tiktok_username(uid, tiktok_user)
                    log.info("TikTok username saved: %s", tiktok_user)
            if state == "instagram":
                ig_user = fetch_instagram_username(code)
                if ig_user:
                    save_instagram_username(uid, ig_user)
                    log.info("Instagram username saved: %s", ig_user)
            if state == "facebook":
                fb_name = fetch_facebook_name(code)
                if fb_name:
                    save_facebook_name(uid, fb_name)
                    log.info("Facebook name saved: %s", fb_name)
            if state == "threads":
                threads_user = fetch_threads_username(code)
                if threads_user:
                    save_threads_username(uid, threads_user)
                    log.info("Threads username saved: %s", threads_user)
            if state == "linkedin":
                li_name = fetch_linkedin_name(code)
                if li_name:
                    save_linkedin_name(uid, li_name)
                    log.info("LinkedIn name saved: %s", li_name)
            if state == "gbp":
                gbp_name = fetch_gbp_account_name(code)
                if gbp_name:
                    save_gbp_account_name(uid, gbp_name)
                    log.info("GBP account name saved: %s", gbp_name)
            if state == "twitter":
                code_verifier = session.pop("x_code_verifier", "")
                if code_verifier:
                    x_user = fetch_twitter_username(code, code_verifier)
                    if x_user:
                        save_twitter_username(uid, x_user)
                        log.info("X username saved: %s", x_user)
        connected = session.get("connected_platforms", [])
        if state not in connected:
            connected.append(state)
        session["connected_platforms"] = connected
    return render_template("oauth_callback.html", code=code, state=state, error=error)


@app.route("/oauth/disconnect")
def oauth_disconnect():
    if not session.get("user"):
        return redirect(url_for("login"))
    platform = request.args.get("platform", "").strip()
    if platform:
        uid = session["user"]["uid"]
        remove_connected_platform(uid, platform)
        connected = session.get("connected_platforms", [])
        if platform in connected:
            connected.remove(platform)
        session["connected_platforms"] = connected
    return redirect(url_for("dashboard"))



@app.route("/bluesky/connect", methods=["POST"])
def bluesky_connect():
    if not session.get("user"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    handle = data.get("handle", "").strip().lstrip("@")
    app_password = data.get("app_password", "").strip()
    if not handle or not app_password:
        return jsonify({"ok": False, "error": "Handle and App Password are required."}), 400
    import requests as http_requests
    try:
        resp = http_requests.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": app_password},
            timeout=15,
        )
        if not resp.ok:
            return jsonify({"ok": False, "error": "Invalid handle or app password."}), 401
    except Exception:
        return jsonify({"ok": False, "error": "Could not reach Bluesky. Try again later."}), 502
    uid = session["user"]["uid"]
    save_connected_platform(uid, "bluesky")
    save_bluesky_handle(uid, handle)
    connected = session.get("connected_platforms", [])
    if "bluesky" not in connected:
        connected.append("bluesky")
    session["connected_platforms"] = connected
    log.info("Bluesky connected for user %s (handle: %s)", uid, handle)
    return jsonify({"ok": True})


@app.route("/bluesky/disconnect")
def bluesky_disconnect():
    if not session.get("user"):
        return redirect(url_for("login"))
    uid = session["user"]["uid"]
    remove_connected_platform(uid, "bluesky")
    connected = session.get("connected_platforms", [])
    if "bluesky" in connected:
        connected.remove("bluesky")
    session["connected_platforms"] = connected
    return redirect(url_for("dashboard"))


@app.route("/drive/folder", methods=["POST"])
def drive_folder():
    if not session.get("user"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    folder_name = data.get("folder_name", "").strip()
    uid = session["user"]["uid"]
    save_drive_folder(uid, folder_name)
    log.info("Drive folder saved for %s: %s", uid, folder_name)
    return jsonify({"ok": True})


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
