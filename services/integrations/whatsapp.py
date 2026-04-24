from datetime import datetime

import requests

from config import Config
from services.database_service import (
    set_integration, get_integration, mark_integration_sync,
    add_knowledge_entry,
)
from services.embedding_service import embed


def base_url() -> str:
    return Config.WHATSAPP_BRIDGE_URL.rstrip("/")


def _bridge_get(path: str, params: dict | None = None, timeout: int = 20) -> dict:
    r = requests.get(f"{base_url()}{path}", params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _bridge_post(path: str, json: dict | None = None, timeout: int = 20) -> dict:
    r = requests.post(f"{base_url()}{path}", json=json or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def status() -> dict:
    """Returns {connected: bool, qr: str|None, me: str|None}."""
    try:
        return _bridge_get("/status", timeout=10)
    except requests.exceptions.RequestException as e:
        return {"connected": False, "error": f"bridge unreachable: {e}", "qr": None, "me": None}


def qr() -> dict:
    """Returns {qr: string (terminal or data URL) or null}."""
    try:
        return _bridge_get("/qr", timeout=10)
    except requests.exceptions.RequestException as e:
        return {"qr": None, "error": f"bridge unreachable: {e}"}


def logout():
    try:
        return _bridge_post("/logout", timeout=10)
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": str(e)}


def ensure_registered(user_id: str) -> bool:
    """Make sure user.integrations.whatsapp exists if the bridge is connected.

    Called from chat (to decide whether to expose the whatsapp_* tools) and
    from the /integrations/status route (so the UI reflects it immediately
    after a successful QR scan, without needing a /sync).
    """
    if get_integration(user_id, "whatsapp"):
        return True
    s = status()
    if not s.get("connected"):
        return False
    set_integration(user_id, "whatsapp", {
        "connected_at": datetime.utcnow(),
        "me": s.get("me"),
    })
    return True


def send_message(to: str, message: str) -> dict:
    """Send a WhatsApp message on the user's behalf via the sidecar."""
    to = (to or "").strip()
    message = (message or "").strip()
    if not to or not message:
        return {"ok": False, "error": "to and message required"}
    try:
        r = requests.post(
            f"{base_url()}/send",
            json={"to": to, "message": message},
            timeout=30,
        )
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not r.ok:
            return {"ok": False, "error": data.get("error") or f"HTTP {r.status_code}"}
        return data or {"ok": True}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"bridge unreachable: {e}"}


def recent_messages(limit_total: int = 10, only: str | None = None) -> list[dict]:
    """Flat list of most recent WhatsApp messages, newest first.

    Prefers the sidecar's /live event buffer (captured as messages come and go,
    so a just-sent message shows up immediately). Falls back to /export when
    the live buffer is empty (e.g. right after bridge start, before any
    message_create event has fired).
    """
    cap = max(1, min(50, int(limit_total)))
    params = {"limit": cap}
    if only in ("sent", "received"):
        params["only"] = only

    # --- 1. Live buffer ---
    try:
        data = _bridge_get("/live", params, timeout=10)
        live = data.get("messages") or []
        if live:
            return [{
                "chat": m.get("chat_name"),
                "is_group": bool(m.get("is_group")),
                "from_me": bool(m.get("from_me")),
                "author": m.get("author") or (m.get("chat_name") if not m.get("from_me") else None),
                "body": (m.get("body") or "")[:500],
                "timestamp": m.get("timestamp"),
            } for m in live[:cap]]
    except requests.exceptions.RequestException:
        pass

    # --- 2. Fallback: /export ---
    try:
        data = _bridge_get(
            "/export",
            {"chats": 15, "per_chat": 10},
            timeout=25,
        )
    except requests.exceptions.RequestException:
        return []
    out: list[dict] = []
    for chat in (data.get("chats") or []):
        name = chat.get("name")
        is_group = bool(chat.get("is_group"))
        for m in (chat.get("messages") or []):
            body = (m.get("body") or "").strip()
            if not body:
                continue
            if only == "sent" and not m.get("from_me"):
                continue
            if only == "received" and m.get("from_me"):
                continue
            out.append({
                "chat": name,
                "is_group": is_group,
                "from_me": bool(m.get("from_me")),
                "author": m.get("author") or (name if not m.get("from_me") else None),
                "body": body[:500],
                "timestamp": m.get("timestamp"),
            })
    out.sort(key=lambda x: x.get("timestamp") or 0, reverse=True)
    return out[:cap]


def list_recent_chats(limit: int = 15) -> list[dict]:
    """Metadata-only listing (no message bodies) for chat/lookup tools."""
    try:
        data = _bridge_get("/export", {"chats": limit, "per_chat": 1}, timeout=20)
    except requests.exceptions.RequestException as e:
        return []
    out = []
    for chat in (data.get("chats") or []):
        out.append({
            "name": chat.get("name"),
            "is_group": bool(chat.get("is_group")),
            "unread": chat.get("unread", 0),
        })
    return out


def sync(user_id: str, chat_limit: int = 15, per_chat_limit: int = 40) -> int:
    """Pull recent messages from the sidecar and ingest them as knowledge.

    Each chat becomes ONE knowledge entry containing a condensed transcript —
    cheaper to embed and easier for RAG to surface as a unit.
    """
    try:
        data = _bridge_get("/export", {
            "chats": chat_limit,
            "per_chat": per_chat_limit,
        }, timeout=60)
    except requests.exceptions.RequestException as e:
        print(f"[whatsapp] export failed: {e}")
        return 0

    chats = data.get("chats") or []
    if not chats:
        # Still record a sync so the UI updates.
        me = data.get("me")
        record = get_integration(user_id, "whatsapp") or {}
        record["connected_at"] = record.get("connected_at") or datetime.utcnow()
        if me:
            record["me"] = me
        set_integration(user_id, "whatsapp", record)
        mark_integration_sync(user_id, "whatsapp", 0)
        return 0

    ingested = 0
    me_number = data.get("me") or ""
    outgoing_bodies: list[str] = []  # collected across all chats for a texting-style sample

    for chat in chats:
        name = chat.get("name") or chat.get("id") or "Unknown chat"
        is_group = bool(chat.get("is_group"))
        messages = chat.get("messages") or []
        if not messages:
            continue

        lines = [f"WhatsApp chat with {name}" + (" (group)" if is_group else "") + ":"]
        for m in messages[-per_chat_limit:]:
            who = "me" if m.get("from_me") else (m.get("author") or name)
            body = (m.get("body") or "").strip()
            if not body:
                continue
            body = body.replace("\n", " ").strip()
            if len(body) > 300:
                body = body[:300] + "…"
            lines.append(f"{who}: {body}")

            if m.get("from_me") and not is_group:
                # Only count one-on-one outgoing messages for style samples —
                # group broadcasts are typically less representative of voice.
                outgoing_bodies.append(body)

        if len(lines) <= 1:
            continue

        text = "\n".join(lines)
        try:
            add_knowledge_entry(user_id, {
                "source": "whatsapp",
                "type": "whatsapp_chat",
                "text": text,
                "embedding": embed(text),
                "chat_name": name,
                "is_group": is_group,
            })
            ingested += 1
        except Exception as e:
            print(f"[whatsapp] embed/store failed for {name}: {e}")

    # Separate "this is how the user actually texts" sample. Dedup + cap to
    # the most recent 60 distinct lines so it stays cheap to embed and
    # high-signal for the twin's voice.
    if outgoing_bodies:
        seen: set = set()
        deduped: list[str] = []
        for b in reversed(outgoing_bodies):  # newest first
            key = b.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(b)
            if len(deduped) >= 60:
                break
        deduped.reverse()  # chronological
        style_text = (
            "How I actually text on WhatsApp (real outgoing messages — slang, "
            "punctuation, casing, emoji and all). Use this when matching my tone:\n"
            + "\n".join(f"• {b}" for b in deduped)
        )
        try:
            add_knowledge_entry(user_id, {
                "source": "whatsapp",
                "type": "whatsapp_texting_style",
                "text": style_text,
                "embedding": embed(style_text),
            })
            ingested += 1
        except Exception as e:
            print(f"[whatsapp] style embed failed: {e}")

    record = get_integration(user_id, "whatsapp") or {}
    if me_number:
        record["me"] = me_number
    record["connected_at"] = record.get("connected_at") or datetime.utcnow()
    set_integration(user_id, "whatsapp", record)
    mark_integration_sync(user_id, "whatsapp", ingested)
    return ingested
