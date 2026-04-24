import base64
import secrets
import time
import urllib.parse
from datetime import datetime

import requests

from config import Config
from services.database_service import (
    set_integration, get_integration, mark_integration_sync,
    add_knowledge_entry,
)
from services.embedding_service import embed


AUTH_URL  = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_ROOT  = "https://api.spotify.com/v1"


def is_configured() -> bool:
    return bool(Config.SPOTIFY_CLIENT_ID and Config.SPOTIFY_CLIENT_SECRET)


def redirect_uri() -> str:
    return f"{Config.APP_BASE_URL.rstrip('/')}/integrations/spotify/callback"


def authorize_url(state: str) -> str:
    params = {
        "client_id": Config.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "scope": Config.SPOTIFY_SCOPES,
        "state": state,
        "show_dialog": "false",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def _basic_auth_header() -> str:
    raw = f"{Config.SPOTIFY_CLIENT_ID}:{Config.SPOTIFY_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def exchange_code(code: str) -> dict:
    r = requests.post(
        TOKEN_URL,
        headers={"Authorization": _basic_auth_header()},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(),
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def refresh_access_token(refresh_token: str) -> dict:
    r = requests.post(
        TOKEN_URL,
        headers={"Authorization": _basic_auth_header()},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _access_token(user_id: str) -> str | None:
    record = get_integration(user_id, "spotify")
    if not record:
        return None
    # Refresh if expiring within the next 60 seconds.
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
        # Spotify sometimes rotates the refresh token.
        if data.get("refresh_token"):
            record["refresh_token"] = data["refresh_token"]
        set_integration(user_id, "spotify", record)
    return record.get("access_token")


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
        record["display_name"] = profile.get("display_name") or profile.get("id")
        record["profile_url"] = (profile.get("external_urls") or {}).get("spotify")
    set_integration(user_id, "spotify", record)


def get_profile(access_token: str) -> dict:
    r = requests.get(
        f"{API_ROOT}/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _api_get(access_token: str, path: str, params: dict | None = None) -> dict:
    r = requests.get(
        f"{API_ROOT}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params or {},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _format_track(t: dict) -> str:
    name = t.get("name", "?")
    artists = ", ".join(a.get("name", "") for a in (t.get("artists") or []))
    album = (t.get("album") or {}).get("name") or ""
    return f"{name} — {artists}" + (f" ({album})" if album else "")


def sync(user_id: str) -> int:
    """Pull listening data into knowledge. Returns number of entries ingested."""
    token = _access_token(user_id)
    if not token:
        return 0

    ingested = 0

    try:
        top_short = _api_get(token, "/me/top/tracks", {"time_range": "short_term", "limit": 20})
        items = top_short.get("items", [])
        if items:
            text = "Top tracks last ~4 weeks:\n" + "\n".join(f"- {_format_track(t)}" for t in items)
            add_knowledge_entry(user_id, {
                "source": "spotify", "type": "spotify_top_tracks_short",
                "text": text, "embedding": embed(text),
            })
            ingested += 1
    except requests.HTTPError as e:
        print(f"[spotify] top tracks short failed: {e}")

    try:
        top_med = _api_get(token, "/me/top/tracks", {"time_range": "medium_term", "limit": 20})
        items = top_med.get("items", [])
        if items:
            text = "Top tracks last ~6 months:\n" + "\n".join(f"- {_format_track(t)}" for t in items)
            add_knowledge_entry(user_id, {
                "source": "spotify", "type": "spotify_top_tracks_medium",
                "text": text, "embedding": embed(text),
            })
            ingested += 1
    except requests.HTTPError as e:
        print(f"[spotify] top tracks medium failed: {e}")

    try:
        top_artists = _api_get(token, "/me/top/artists", {"time_range": "medium_term", "limit": 15})
        items = top_artists.get("items", [])
        if items:
            lines = []
            for a in items:
                genres = ", ".join((a.get("genres") or [])[:4])
                line = f"- {a.get('name','?')}"
                if genres:
                    line += f" [{genres}]"
                lines.append(line)
            text = "Top artists last ~6 months:\n" + "\n".join(lines)
            add_knowledge_entry(user_id, {
                "source": "spotify", "type": "spotify_top_artists",
                "text": text, "embedding": embed(text),
            })
            ingested += 1
    except requests.HTTPError as e:
        print(f"[spotify] top artists failed: {e}")

    try:
        recent = _api_get(token, "/me/player/recently-played", {"limit": 50})
        items = [it.get("track") for it in (recent.get("items") or []) if it.get("track")]
        track_ids = [t.get("id") for t in items if t.get("id")]
        features_by_id = {}
        if track_ids:
            feats = _api_get(token, "/audio-features", {"ids": ",".join(track_ids[:50])})
            for f in (feats.get("audio_features") or []):
                if f and f.get("id"):
                    features_by_id[f["id"]] = f
        if items:
            valences = [features_by_id[t["id"]]["valence"] for t in items if t.get("id") in features_by_id]
            energies = [features_by_id[t["id"]]["energy"]  for t in items if t.get("id") in features_by_id]
            avg_val = round(sum(valences) / len(valences), 2) if valences else None
            avg_eng = round(sum(energies) / len(energies), 2) if energies else None
            lines = [f"- {_format_track(t)}" for t in items[:25]]
            mood_line = ""
            if avg_val is not None:
                mood_line = f"\n\nAudio feature averages — valence (musical positivity): {avg_val}, energy: {avg_eng}."
            text = "Recently played (most recent first):\n" + "\n".join(lines) + mood_line
            add_knowledge_entry(user_id, {
                "source": "spotify", "type": "spotify_recent_played",
                "text": text, "embedding": embed(text),
            })
            ingested += 1
    except requests.HTTPError as e:
        print(f"[spotify] recent played failed: {e}")

    mark_integration_sync(user_id, "spotify", ingested)
    return ingested
