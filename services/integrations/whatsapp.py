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
            # Keep each line compact.
            body = body.replace("\n", " ").strip()
            if len(body) > 300:
                body = body[:300] + "…"
            lines.append(f"{who}: {body}")

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

    record = get_integration(user_id, "whatsapp") or {}
    if me_number:
        record["me"] = me_number
    record["connected_at"] = record.get("connected_at") or datetime.utcnow()
    set_integration(user_id, "whatsapp", record)
    mark_integration_sync(user_id, "whatsapp", ingested)
    return ingested
