import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

from config import Config
from services.database_service import (
    set_integration, get_integration, mark_integration_sync,
    add_knowledge_entry,
)
from services.embedding_service import embed


AUTH_URL  = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_ROOT  = "https://api.github.com"


def is_configured() -> bool:
    return bool(Config.GITHUB_CLIENT_ID and Config.GITHUB_CLIENT_SECRET)


def redirect_uri() -> str:
    return f"{Config.APP_BASE_URL.rstrip('/')}/integrations/github/callback"


def authorize_url(state: str) -> str:
    params = {
        "client_id": Config.GITHUB_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "scope": Config.GITHUB_SCOPES,
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str) -> dict:
    r = requests.post(
        TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": Config.GITHUB_CLIENT_ID,
            "client_secret": Config.GITHUB_CLIENT_SECRET,
            "code": code,
            "redirect_uri": redirect_uri(),
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data.get("error_description") or data["error"])
    return data


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_profile(access_token: str) -> dict:
    r = requests.get(f"{API_ROOT}/user", headers=_headers(access_token), timeout=15)
    r.raise_for_status()
    return r.json()


def store_tokens(user_id: str, token_response: dict, profile: dict | None = None):
    record = {
        "access_token": token_response["access_token"],
        "scope": token_response.get("scope", ""),
        "connected_at": datetime.utcnow(),
    }
    if profile:
        record["username"] = profile.get("login")
        record["profile_url"] = profile.get("html_url")
    set_integration(user_id, "github", record)


def _access_token(user_id: str) -> str | None:
    record = get_integration(user_id, "github")
    return record.get("access_token") if record else None


def sync(user_id: str) -> int:
    token = _access_token(user_id)
    if not token:
        return 0
    record = get_integration(user_id, "github") or {}
    username = record.get("username")
    if not username:
        try:
            profile = get_profile(token)
            username = profile.get("login")
        except requests.HTTPError as e:
            print(f"[github] fetch user failed: {e}")
            return 0

    ingested = 0
    h = _headers(token)

    # --- Profile + bio ---
    try:
        profile = get_profile(token)
        bio_parts = []
        if profile.get("name"): bio_parts.append(f"Name: {profile['name']}")
        if profile.get("bio"):  bio_parts.append(f"Bio: {profile['bio']}")
        if profile.get("company"): bio_parts.append(f"Company: {profile['company']}")
        if profile.get("location"): bio_parts.append(f"Location: {profile['location']}")
        if profile.get("public_repos") is not None:
            bio_parts.append(f"Public repos: {profile['public_repos']}")
        if profile.get("followers") is not None:
            bio_parts.append(f"Followers: {profile['followers']}")
        if bio_parts:
            text = f"GitHub profile @{username}:\n" + "\n".join(bio_parts)
            add_knowledge_entry(user_id, {
                "source": "github", "type": "github_profile",
                "text": text, "embedding": embed(text),
            })
            ingested += 1
    except requests.HTTPError as e:
        print(f"[github] profile failed: {e}")

    # --- Recent owned repos (names + descriptions) ---
    try:
        r = requests.get(
            f"{API_ROOT}/user/repos",
            headers=h,
            params={"sort": "updated", "per_page": 30, "affiliation": "owner"},
            timeout=15,
        )
        r.raise_for_status()
        repos = r.json()
        if repos:
            lines = []
            for repo in repos[:20]:
                line = f"- {repo.get('name','?')}"
                lang = repo.get("language")
                if lang: line += f" [{lang}]"
                if repo.get("description"):
                    line += f": {repo['description']}"
                lines.append(line)
            text = f"Recent repos of @{username} (most recently updated first):\n" + "\n".join(lines)
            add_knowledge_entry(user_id, {
                "source": "github", "type": "github_repos",
                "text": text, "embedding": embed(text),
            })
            ingested += 1
    except requests.HTTPError as e:
        print(f"[github] repos failed: {e}")

    # --- Recent commit messages (last ~30 days) via search ---
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = requests.get(
            f"{API_ROOT}/search/commits",
            headers={**h, "Accept": "application/vnd.github.cloak-preview+json"},
            params={
                "q": f"author:{username} committer-date:>{since}",
                "sort": "committer-date",
                "order": "desc",
                "per_page": 40,
            },
            timeout=15,
        )
        if r.ok:
            items = (r.json().get("items") or [])
            if items:
                lines = []
                for c in items[:30]:
                    msg = (c.get("commit") or {}).get("message", "").split("\n", 1)[0][:140]
                    repo_name = ((c.get("repository") or {}).get("full_name")) or "?"
                    lines.append(f"- [{repo_name}] {msg}")
                text = f"Recent commits by @{username} (last 30 days):\n" + "\n".join(lines)
                add_knowledge_entry(user_id, {
                    "source": "github", "type": "github_commits",
                    "text": text, "embedding": embed(text),
                })
                ingested += 1
    except requests.HTTPError as e:
        print(f"[github] commits search failed: {e}")

    mark_integration_sync(user_id, "github", ingested)
    return ingested
