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


# ================= Tool-callable read methods =================
#
# These are intentionally small, LLM-friendly wrappers used by the chat
# tool-calling pipeline in twin_service. Each returns plain dicts/lists
# that serialize cleanly to JSON.

def is_connected(user_id: str) -> bool:
    record = get_integration(user_id, "spotify")
    return bool(record and record.get("access_token"))


def _track_summary(t: dict) -> dict:
    if not t:
        return {}
    return {
        "id": t.get("id"),
        "name": t.get("name"),
        "artists": [a.get("name", "") for a in (t.get("artists") or [])],
        "album": (t.get("album") or {}).get("name", ""),
        "popularity": t.get("popularity"),
        "spotify_url": (t.get("external_urls") or {}).get("spotify"),
        "preview_url": t.get("preview_url"),
    }


def _artist_summary(a: dict) -> dict:
    if not a:
        return {}
    return {
        "id": a.get("id"),
        "name": a.get("name"),
        "genres": (a.get("genres") or [])[:6],
        "popularity": a.get("popularity"),
        "spotify_url": (a.get("external_urls") or {}).get("spotify"),
    }


def get_top_tracks(user_id: str, time_range: str = "medium_term", limit: int = 10) -> list[dict]:
    token = _access_token(user_id)
    if not token:
        return []
    limit = max(1, min(20, int(limit or 10)))
    try:
        data = _api_get(token, "/me/top/tracks", {"time_range": time_range, "limit": limit})
    except requests.HTTPError:
        return []
    return [_track_summary(t) for t in (data.get("items") or [])]


def get_top_artists(user_id: str, time_range: str = "medium_term", limit: int = 10) -> list[dict]:
    token = _access_token(user_id)
    if not token:
        return []
    limit = max(1, min(20, int(limit or 10)))
    try:
        data = _api_get(token, "/me/top/artists", {"time_range": time_range, "limit": limit})
    except requests.HTTPError:
        return []
    return [_artist_summary(a) for a in (data.get("items") or [])]


def get_recent_tracks(user_id: str, limit: int = 15) -> list[dict]:
    token = _access_token(user_id)
    if not token:
        return []
    limit = max(1, min(50, int(limit or 15)))
    try:
        data = _api_get(token, "/me/player/recently-played", {"limit": limit})
    except requests.HTTPError:
        return []
    out = []
    for it in (data.get("items") or []):
        t = it.get("track") or {}
        s = _track_summary(t)
        s["played_at"] = it.get("played_at")
        out.append(s)
    return out


def get_taste_profile(user_id: str) -> dict:
    """Genres + average audio features across recent plays."""
    token = _access_token(user_id)
    if not token:
        return {}
    profile: dict = {"top_genres": [], "top_artists": [], "audio_features": {}}

    try:
        artists = _api_get(token, "/me/top/artists", {"time_range": "medium_term", "limit": 20})
        genre_counts: dict = {}
        for a in (artists.get("items") or []):
            profile["top_artists"].append(a.get("name"))
            for g in (a.get("genres") or []):
                genre_counts[g] = genre_counts.get(g, 0) + 1
        profile["top_genres"] = [
            g for g, _ in sorted(genre_counts.items(), key=lambda kv: -kv[1])[:10]
        ]
    except requests.HTTPError:
        pass

    try:
        recent = _api_get(token, "/me/player/recently-played", {"limit": 50})
        ids = [
            (it.get("track") or {}).get("id")
            for it in (recent.get("items") or [])
            if (it.get("track") or {}).get("id")
        ]
        if ids:
            feats = _api_get(token, "/audio-features", {"ids": ",".join(ids[:50])})
            features = [f for f in (feats.get("audio_features") or []) if f]
            def avg(k):
                xs = [f[k] for f in features if k in f and f[k] is not None]
                return round(sum(xs) / len(xs), 2) if xs else None
            profile["audio_features"] = {
                "valence": avg("valence"),        # 0 sad → 1 happy
                "energy": avg("energy"),          # 0 calm → 1 intense
                "danceability": avg("danceability"),
                "tempo": round(avg("tempo") or 0) if avg("tempo") else None,
                "acousticness": avg("acousticness"),
                "instrumentalness": avg("instrumentalness"),
            }
    except requests.HTTPError:
        pass

    profile["top_artists"] = profile["top_artists"][:8]
    return profile


def recommend_tracks(
    user_id: str,
    seed_tracks: list[str] | None = None,
    seed_artists: list[str] | None = None,
    seed_genres: list[str] | None = None,
    target_valence: float | None = None,
    target_energy: float | None = None,
    target_danceability: float | None = None,
    limit: int = 5,
) -> dict:
    """Calls Spotify's /recommendations. NOTE: Spotify deprecated this endpoint
    for apps created after Nov 2024. If it fails, we return an empty list plus
    an explanation — the LLM is expected to fall back to recommending from
    its own knowledge using the user's taste profile.
    """
    token = _access_token(user_id)
    if not token:
        return {"tracks": [], "error": "spotify_not_connected"}

    # Need at least one seed; auto-fill from top tracks if none provided.
    if not (seed_tracks or seed_artists or seed_genres):
        tops = get_top_tracks(user_id, "medium_term", 5)
        seed_tracks = [t["id"] for t in tops if t.get("id")][:3]
        if not seed_tracks:
            return {"tracks": [], "error": "no_seeds"}

    params: dict = {"limit": max(1, min(10, int(limit or 5)))}
    if seed_tracks:  params["seed_tracks"]  = ",".join(seed_tracks[:5])
    if seed_artists: params["seed_artists"] = ",".join(seed_artists[:5])
    if seed_genres:  params["seed_genres"]  = ",".join(seed_genres[:5])
    if target_valence      is not None: params["target_valence"] = max(0.0, min(1.0, target_valence))
    if target_energy       is not None: params["target_energy"]  = max(0.0, min(1.0, target_energy))
    if target_danceability is not None: params["target_danceability"] = max(0.0, min(1.0, target_danceability))

    try:
        data = _api_get(token, "/recommendations", params)
    except requests.HTTPError as e:
        # 404/403 here typically means the app was created after the API
        # deprecation date. The LLM should catch on and fall back.
        return {"tracks": [], "error": f"spotify_api_error:{e.response.status_code if e.response else 'unknown'}"}

    return {"tracks": [_track_summary(t) for t in (data.get("tracks") or [])]}


def search_track(user_id: str, query: str, limit: int = 5) -> list[dict]:
    """Look up tracks by name — useful when the LLM suggests a song from its
    own knowledge and we want a real Spotify URL to link."""
    token = _access_token(user_id)
    if not token or not query.strip():
        return []
    try:
        data = _api_get(token, "/search", {"q": query, "type": "track", "limit": max(1, min(10, int(limit)))})
    except requests.HTTPError:
        return []
    return [_track_summary(t) for t in ((data.get("tracks") or {}).get("items") or [])]


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
