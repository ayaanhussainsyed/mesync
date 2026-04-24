import base64
import time
import urllib.parse
from datetime import datetime
from email.message import EmailMessage

import requests

from config import Config
from services.database_service import (
    set_integration, get_integration, mark_integration_sync,
    add_knowledge_entry,
)
from services.embedding_service import embed


AUTH_URL   = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL  = "https://oauth2.googleapis.com/token"
API_ROOT   = "https://gmail.googleapis.com/gmail/v1"
USERINFO   = "https://www.googleapis.com/oauth2/v3/userinfo"


def is_configured() -> bool:
    return bool(Config.GMAIL_CLIENT_ID and Config.GMAIL_CLIENT_SECRET)


def redirect_uri() -> str:
    return f"{Config.APP_BASE_URL.rstrip('/')}/integrations/gmail/callback"


def authorize_url(state: str) -> str:
    params = {
        "client_id": Config.GMAIL_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "scope": Config.GMAIL_SCOPES,
        "access_type": "offline",  # we need a refresh token
        "prompt": "consent",       # force refresh_token to be issued every time
        "state": state,
        "include_granted_scopes": "true",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str) -> dict:
    r = requests.post(
        TOKEN_URL,
        data={
            "client_id": Config.GMAIL_CLIENT_ID,
            "client_secret": Config.GMAIL_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(),
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def refresh_access_token(refresh_token: str) -> dict:
    r = requests.post(
        TOKEN_URL,
        data={
            "client_id": Config.GMAIL_CLIENT_ID,
            "client_secret": Config.GMAIL_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _access_token(user_id: str) -> str | None:
    record = get_integration(user_id, "gmail")
    if not record:
        return None
    expires_at = record.get("expires_at", 0)
    if time.time() >= (expires_at - 60):
        refresh = record.get("refresh_token")
        if not refresh:
            return None
        try:
            data = refresh_access_token(refresh)
        except requests.HTTPError:
            return None
        record["access_token"] = data["access_token"]
        record["expires_at"] = int(time.time()) + int(data.get("expires_in", 3600))
        set_integration(user_id, "gmail", record)
    return record.get("access_token")


def get_profile(access_token: str) -> dict:
    """Returns {email, name} from the OIDC userinfo endpoint."""
    r = requests.get(
        USERINFO,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def store_tokens(user_id: str, token_response: dict, profile: dict | None = None):
    now = int(time.time())
    record = {
        "access_token": token_response["access_token"],
        "refresh_token": token_response.get("refresh_token", ""),
        "expires_at": now + int(token_response.get("expires_in", 3600)),
        "scope": token_response.get("scope", ""),
        "connected_at": datetime.utcnow(),
    }
    if profile:
        record["email"] = profile.get("email")
        record["name"] = profile.get("name")
    # Preserve existing refresh_token if this response didn't return one
    # (happens after the first consent when no prompt=consent).
    if not record["refresh_token"]:
        existing = get_integration(user_id, "gmail") or {}
        if existing.get("refresh_token"):
            record["refresh_token"] = existing["refresh_token"]
    set_integration(user_id, "gmail", record)


def _gmail_get(access_token: str, path: str, params: dict | None = None) -> dict:
    r = requests.get(
        f"{API_ROOT}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params or {},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _header(msg: dict, name: str) -> str:
    headers = ((msg.get("payload") or {}).get("headers")) or []
    name_lower = name.lower()
    for h in headers:
        if (h.get("name") or "").lower() == name_lower:
            return h.get("value", "")
    return ""


# ================= Tool-callable methods =================

def is_connected(user_id: str) -> bool:
    record = get_integration(user_id, "gmail")
    return bool(record and record.get("access_token"))


def _message_summary(token: str, mid: str, include_snippet: bool = True) -> dict | None:
    try:
        msg = _gmail_get(
            token, f"/users/me/messages/{mid}",
            {"format": "metadata",
             "metadataHeaders": ["Subject", "From", "To", "Date"]},
        )
    except requests.HTTPError:
        return None
    return {
        "id": mid,
        "thread_id": msg.get("threadId"),
        "subject": _header(msg, "Subject") or "(no subject)",
        "from": _header(msg, "From"),
        "to": _header(msg, "To"),
        "date": _header(msg, "Date"),
        "snippet": (msg.get("snippet") or "").strip() if include_snippet else None,
        "unread": "UNREAD" in (msg.get("labelIds") or []),
    }


def list_recent(user_id: str, limit: int = 10, unread_only: bool = False, query: str | None = None) -> list[dict]:
    """Return recent inbox messages (or results of a Gmail search query).

    query uses Gmail's native search syntax — e.g. 'from:boss@x.com', 'is:unread',
    'subject:invoice newer_than:7d'. Defaults to the inbox when empty.
    """
    token = _access_token(user_id)
    if not token:
        return []
    params: dict = {"maxResults": max(1, min(25, int(limit or 10)))}
    q_parts = []
    if query:
        q_parts.append(query)
    if unread_only:
        q_parts.append("is:unread")
    if q_parts:
        params["q"] = " ".join(q_parts)
    else:
        params["labelIds"] = "INBOX"
    try:
        listing = _gmail_get(token, "/users/me/messages", params)
    except requests.HTTPError as e:
        return [{"error": f"list failed: {e}"}]
    ids = [m.get("id") for m in (listing.get("messages") or []) if m.get("id")]
    out: list[dict] = []
    for mid in ids:
        s = _message_summary(token, mid)
        if s:
            out.append(s)
    return out


def read_message(user_id: str, message_id: str) -> dict:
    """Full body of a single message (plain text if available)."""
    token = _access_token(user_id)
    if not token:
        return {"error": "not connected"}
    try:
        msg = _gmail_get(
            token, f"/users/me/messages/{message_id}",
            {"format": "full"},
        )
    except requests.HTTPError as e:
        return {"error": f"fetch failed: {e}"}

    def walk(part):
        if not part:
            return ""
        mime = part.get("mimeType") or ""
        body = (part.get("body") or {})
        data = body.get("data")
        if mime == "text/plain" and data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            except Exception:
                return ""
        # Recurse into multipart
        for child in (part.get("parts") or []):
            text = walk(child)
            if text:
                return text
        # Last resort: decode HTML stripped of tags.
        if mime == "text/html" and data:
            try:
                import re
                raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                return re.sub(r"<[^>]+>", "", raw)
            except Exception:
                return ""
        return ""

    body_text = walk(msg.get("payload") or {}).strip()
    if len(body_text) > 6000:
        body_text = body_text[:6000] + "…"

    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "subject": _header(msg, "Subject"),
        "from": _header(msg, "From"),
        "to": _header(msg, "To"),
        "date": _header(msg, "Date"),
        "snippet": msg.get("snippet"),
        "body": body_text,
    }


def send_email(user_id: str, to: str, subject: str, body: str,
               cc: str | None = None, bcc: str | None = None) -> dict:
    """Send an email from the user's Gmail. Returns {ok, id?, error?}."""
    token = _access_token(user_id)
    if not token:
        return {"ok": False, "error": "not connected"}
    to = (to or "").strip()
    if not to or "@" not in to:
        return {"ok": False, "error": "invalid recipient"}
    if not (body or "").strip():
        return {"ok": False, "error": "empty body"}

    msg = EmailMessage()
    msg["To"] = to
    if cc:  msg["Cc"]  = cc
    if bcc: msg["Bcc"] = bcc
    msg["Subject"] = subject or "(no subject)"
    # From header is filled in server-side by Gmail; we still want a sane fallback.
    record = get_integration(user_id, "gmail") or {}
    if record.get("email"):
        msg["From"] = record["email"]
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")
    try:
        r = requests.post(
            f"{API_ROOT}/users/me/messages/send",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"raw": raw},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return {"ok": True, "id": data.get("id"), "thread_id": data.get("threadId")}
    except requests.HTTPError as e:
        detail = ""
        try:
            detail = e.response.json().get("error", {}).get("message", "")
        except Exception:
            pass
        return {"ok": False, "error": f"send failed ({e.response.status_code if e.response else '?'}) {detail}".strip()}
    except requests.RequestException as e:
        return {"ok": False, "error": f"network error: {e}"}


def sync(user_id: str, count: int = 20) -> int:
    """Pull recent inbox emails into knowledge. Returns number of entries ingested."""
    token = _access_token(user_id)
    if not token:
        return 0

    ingested = 0
    try:
        listing = _gmail_get(
            token, "/users/me/messages",
            {"maxResults": min(50, max(5, count)), "labelIds": "INBOX"}
        )
    except requests.HTTPError as e:
        print(f"[gmail] list failed: {e}")
        return 0

    msg_ids = [m.get("id") for m in (listing.get("messages") or []) if m.get("id")]
    if not msg_ids:
        mark_integration_sync(user_id, "gmail", 0)
        return 0

    lines = []
    for mid in msg_ids:
        try:
            msg = _gmail_get(
                token, f"/users/me/messages/{mid}",
                {"format": "metadata",
                 "metadataHeaders": ["Subject", "From", "Date"]},
            )
        except requests.HTTPError:
            continue
        subject = _header(msg, "Subject") or "(no subject)"
        sender  = _header(msg, "From") or ""
        date    = _header(msg, "Date") or ""
        snippet = (msg.get("snippet") or "").strip()
        if len(snippet) > 260:
            snippet = snippet[:260] + "…"
        # Compact single-line record per email.
        lines.append(f"- [{date}] {subject} — from {sender}\n  {snippet}")

    if lines:
        text = "Recent inbox emails (subject, sender, snippet):\n" + "\n".join(lines)
        try:
            add_knowledge_entry(user_id, {
                "source": "gmail",
                "type": "gmail_inbox",
                "text": text,
                "embedding": embed(text),
            })
            ingested = 1
        except Exception as e:
            print(f"[gmail] embed/store failed: {e}")

    mark_integration_sync(user_id, "gmail", ingested)
    return ingested
