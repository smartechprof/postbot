# Security Guidelines for PostBot Development

## Overview
This document contains security requirements for all code changes to PostBot. Load this file at the start of any development chat to ensure security compliance.

## Core Security Principles

### 1. No Hardcoded Secrets
- All tokens, passwords, API keys MUST use `os.getenv()`
- Never commit `.env` files or credentials to Git
- Use placeholders like `YOUR_TOKEN_HERE` in documentation

### 2. Safe Error Logging
- Never log actual secret values (tokens, passwords)
- Limit error message details to prevent information disclosure
- Use generic messages: "Authentication failed" not "Invalid token: abc123"
- Log lengths/counts, not actual content: `f"caption ({len(caption)} chars)"`

### 3. Input Validation & Sanitization
- Validate all file names for path traversal: `os.path.basename()` + check for `..`
- Validate all user inputs before processing
- Check HTTP response status codes before parsing JSON
- Use `try/except` for JSON parsing

### 4. Shell Injection Prevention (CRITICAL for converter.py)
- Never pass user input directly to shell commands
- Sanitize all file names before ffmpeg calls
- Use `shlex.quote()` for shell arguments
- Validate file extensions and paths
- Example: `shlex.quote(os.path.basename(input_file))`

### 5. File Operations
- Use `with open()` for automatic file closure
- Set explicit encoding: `encoding="utf-8"`
- Use `tempfile` module for temporary files
- Clean up temp files in `finally` blocks

### 6. HTTP Requests
- Always set timeouts on requests
- Check `response.ok` before parsing
- Handle connection errors with retry logic
- Use appropriate HTTP methods (GET for reads, POST for writes)

### 7. Concurrent Access
- Use `fcntl.flock()` for file locking when multiple processes might access same files
- Prefer database transactions over file operations for multi-user scenarios
- Design with future scaling in mind

## Platform-Specific Requirements

### Mandatory Retry Logic (ALL Platforms)
Every platform MUST implement retry logic for network failures:
```python
for attempt in range(3):
    try:
        result = api_call()
        return result
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
        if attempt < 2:
            wait_time = 2 ** attempt * 10  # Exponential backoff: 10s, 20s
            log.warning(f"Network error, retrying in {wait_time}s...")
            time.sleep(wait_time)
            continue
        return {"ok": False, "error": f"Network failure after 3 attempts"}
```

### Required HTTP Request Pattern
```python
def _api_request(url: str, **kwargs) -> dict:
    resp = requests.post(url, timeout=60, **kwargs)
    if not resp.ok:
        return {"error": {"message": f"HTTP {resp.status_code}: {resp.text[:200]}"}}
    try:
        return resp.json()
    except ValueError:
        return {"error": {"message": f"Invalid JSON response: {resp.text[:200]}"}}
```

### Required Elements in ALL Platform Files
- Retry logic with exponential backoff (minimum 3 attempts)
- SAFE_MODE support for testing
- Proper error handling and logging
- Cleanup in `finally` blocks
- Timeout on all network calls
- Token validation before API calls

## Critical Security Checks

### .gitignore Validation
Must contain:
- `.env` and `*.env`
- `state.txt`
- `*.mp4`, `*.mov`, `*.avi` (temp video files)
- `*.tmp`, `temp/`
- `.DS_Store`

### Shell Injection in converter.py
- File names from Google Drive can contain malicious characters
- MUST use `shlex.quote()` on all file paths passed to ffmpeg
- MUST validate file extensions
- Example vulnerability: filename `; rm -rf /` → shell execution

### Metadata Field Validation
- Check `PLATFORM_LIMITS` matches actual platform field names
- YouTube tags validation for list fields
- Field name consistency across platforms

## Future-Proofing for SaaS

### Multi-User Preparation
- Add `user_id: str = "default"` parameters to functions
- Create abstraction functions for data access: `get_user_state_path(user_id)`
- Design for database migration: avoid direct file path hardcoding
- Use structured logging (JSON format preferred)

### Scalability Considerations
- File locking for concurrent access
- Stateless function design
- Clear separation of concerns
- Proper dependency injection

## Testing Security Changes
1. Test with invalid tokens/credentials
2. Test with malformed file names (`../../../etc/passwd`)
3. Test concurrent access scenarios
4. Verify no secrets appear in logs
5. Check error handling paths
6. Test all retry scenarios (network timeouts)

## Pre-Deployment Checklist
- [ ] No hardcoded secrets
- [ ] Error messages don't leak sensitive data
- [ ] File operations use proper paths with sanitization
- [ ] HTTP requests have timeouts AND retry logic
- [ ] JSON parsing is wrapped in try/except
- [ ] Cleanup code in finally blocks
- [ ] File locking for shared resources
- [ ] SAFE_MODE works correctly
- [ ] Shell commands use shlex.quote()
- [ ] All platforms have 3-attempt retry logic

## Auto-Review Commands
```bash
# Check for hardcoded secrets
grep -r "token.*=" . --exclude-dir=.git | grep -v "os.getenv"

# Check for unsafe logging
grep -r "log.*%s.*token\|log.*%s.*password" . --exclude-dir=.git

# Check for missing timeouts
grep -r "requests\." . --exclude-dir=.git | grep -v "timeout"

# Check for unsafe file operations
grep -r "open(" . --exclude-dir=.git | grep -v "with open"

# Check for missing retry logic
grep -L "for attempt in range" platforms/*.py

# Check for shell injection risks
grep -r "subprocess\|os.system" . --exclude-dir=.git
```

Last Updated: 2026-04-17

## Web Application Security (Future Requirements)

### SQL Injection Prevention
**Applies to:** Future web interface, user database, analytics
- Use parameterized queries/prepared statements
- Never concatenate user input into SQL strings
- Validate all input types and ranges
- Use ORM with built-in protection (SQLAlchemy, Django ORM)
- Example: `cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))`

### Cross-Site Scripting (XSS) Prevention  
**Applies to:** Future web dashboard, user content display
- Escape all user output in templates
- Use Content Security Policy (CSP) headers
- Validate and sanitize all form inputs
- Never use `innerHTML` with user data
- Example: `escape(user_input)` before display

### API Security & Authorization
**Applies to:** Future REST API, webhook endpoints, user accounts
- Implement proper authentication (JWT, OAuth)
- Authorize every endpoint based on user permissions
- Rate limiting on all public endpoints
- API key rotation and expiration
- Never expose internal IDs or system details
- Example: `@require_auth` decorator on all routes

### Current PostBot Security (Apply Now)

#### Encrypted Token Storage
**Status:** NOT IMPLEMENTED - tokens in plaintext `/etc/igbot.env`
- Encrypt tokens at rest using system keyring
- Use environment variables only in development
- Production: use AWS Secrets Manager, HashiCorp Vault, or similar
- Rotate tokens regularly

#### Complete Secrets Audit  
**Status:** PARTIALLY COMPLETE - needs deeper review
- Scan entire codebase for hardcoded secrets
- Check logs for accidental token exposure
- Verify no secrets in Git history
- Check temporary files and error dumps


## Lessons Learned (Real Incidents)

### YouTube Tags Length — Real Limit Is Stricter
YouTube API counts tag length **including quotes around multi-word tags**, not raw character count. A tag like `spray foam` is counted as `"spray foam"` = 12 chars, not 10.
- **Empirically verified working limit:** ~460 chars (computed with quotes)
- **API rejects** `invalidTags` / HTTP 400 when total exceeds the internal threshold (observed failure at 546 "chars-with-quotes")
- **Formula for validation:** `sum(len(tag) + (2 if ' ' in tag else 0) for tag in tags)`
- Our `PLATFORM_LIMITS` entry uses 460 as safe ceiling

### OAuth Refresh Tokens — Match Scopes To Actual API Usage
When generating a refresh token, the scope list must include **every scope** the application uses at runtime. A common trap:
- App calls `channels.list(mine=True)` → needs `youtube.readonly` (or `youtube`)
- App calls `videos.insert(...)` → needs `youtube.upload`
- Token with only `youtube.upload` fails with HTTP 403 `insufficientPermissions` on the first call
- **Scopes cannot be added to an existing token** — must regenerate
- **Google Testing mode:** refresh tokens can be revoked unpredictably (official rule: 7 days of inactivity, but in practice triggers happen sooner). Plan for Production verification to avoid weekly token rotation.

### Token Rotation Runbook
If `invalid_grant: Token has been expired or revoked`:
1. Open OAuth consent page in Google Cloud Console, confirm `polynor.geo@gmail.com` is listed as test user
2. Run local script (e.g. `get_youtube_token.py`) on Mac to open browser OAuth flow
3. Replace only the `*_REFRESH_TOKEN` value in `/etc/igbot.env` — keep `CLIENT_ID` and `CLIENT_SECRET` unchanged
4. Verify with a direct API call before running bot in production mode
