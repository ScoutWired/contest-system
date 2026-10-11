"""Discord OAuth2 + guild role verification (server-side, fail closed)."""
import time
import httpx
from urllib.parse import urlencode
from fastapi import APIRouter  # noqa (placeholder to keep module import-safe)
from app.config import settings
from app.security import gen_token

API = "https://discord.com/api/v10"

# test hook: tests monkeypatch fetch_member_roles
def fetch_member_roles(discord_user_id: str) -> set[str] | None:
    """Return set of role ids via bot API, or None if unverifiable (fail closed)."""
    if not settings.DISCORD_BOT_TOKEN or not settings.DISCORD_GUILD_ID:
        return None
    try:
        r = httpx.get(f"{API}/guilds/{settings.DISCORD_GUILD_ID}/members/{discord_user_id}",
                      headers={"Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}"}, timeout=8)
        if r.status_code == 404:
            return set()  # not a member
        if r.status_code == 429:
            return None
        if r.status_code != 200:
            return None
        return set(r.json().get("roles", []))
    except Exception:
        return None

def oauth_url(state: str) -> str:
    redirect = settings.DISCORD_REDIRECT_URI or (settings.APP_URL + "/login/discord/callback")
    q = urlencode({"client_id": settings.DISCORD_CLIENT_ID, "redirect_uri": redirect,
                   "response_type": "code", "scope": "identify", "state": state})
    return f"https://discord.com/oauth2/authorize?{q}"

def exchange_code(code: str) -> dict | None:
    redirect = settings.DISCORD_REDIRECT_URI or (settings.APP_URL + "/login/discord/callback")
    try:
        r = httpx.post(f"{API}/oauth2/token",
                       data={"grant_type": "authorization_code", "code": code,
                             "redirect_uri": redirect},
                       auth=(settings.DISCORD_CLIENT_ID, settings.DISCORD_CLIENT_SECRET),
                       timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        me = httpx.get(f"{API}/users/@me", headers={"Authorization": f"Bearer {data['access_token']}"}, timeout=8)
        if me.status_code != 200:
            return None
        u = me.json()
        return {"discord_id": str(u["id"]), "username": u.get("username", "")}
    except Exception:
        return None

def validate_guild_roles(role_ids: list[str]) -> tuple[bool, str]:
    """Check configured roles exist in guild. Returns (ok, message)."""
    for r in role_ids:
        if not r.isdigit():
            return False, f"Invalid role id format: {r!r} (must be numeric snowflake)"
    if not settings.DISCORD_BOT_TOKEN or not settings.DISCORD_GUILD_ID:
        return True, "Saved. Live guild validation skipped: bot token/guild not configured."
    try:
        r = httpx.get(f"{API}/guilds/{settings.DISCORD_GUILD_ID}/roles",
                      headers={"Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}"}, timeout=8)
        if r.status_code != 200:
            return True, f"Saved. Guild role check unavailable (HTTP {r.status_code}); failing closed on auth checks."
        existing = {str(x["id"]) for x in r.json()}
        missing = [x for x in role_ids if x not in existing]
        if missing:
            return True, f"Saved, but roles not found in guild: {', '.join(missing)}"
        return True, "Saved. All roles verified in guild."
    except Exception as e:
        return True, f"Saved. Guild validation error (failing closed on auth): {e}"
