"""First-run setup wizard: collects required config with instructions/links.

Access: open to anyone while setup is incomplete (fresh install), otherwise
admin-only (re-run). Secrets posted here are used transiently for live
validation and echoed back ONLY as copy-paste .env snippets in the same
response — never written to the DB, logs, or templates.
"""
import re
import httpx
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession
from app.deps import templates, get_db, ctx, site_name
from app.models import AppSetting, OAuthState, RoleConfig, User
from app.auth_sessions import COOKIE, get_current_user
from app import permissions as P
from app.config import settings
from app.services_audit import audit

router = APIRouter()

STEPS = ["welcome", "site", "discord_oauth", "discord_server", "roles",
         "first_admin", "notifications", "finish"]
LABELS = {"welcome": "Welcome", "site": "Site", "discord_oauth": "Discord login",
          "discord_server": "Discord server", "roles": "Roles & permissions",
          "first_admin": "First admin", "notifications": "Notifications", "finish": "Finish"}

LINKS = {
    "dev_portal": "https://discord.com/developers/applications",
    "oauth2_docs": "https://docs.discord.com/developers/topics/oauth2",
    "bot_guide": "https://docs.discord.com/developers/quick-start/getting-started",
    "intents_docs": "https://docs.discord.com/developers/events/gateway",
    "find_ids": "https://support-dev.discord.com/hc/en-us/articles/360028717192-Where-can-I-find-my-Application-Team-Server-ID",
    "pa_portal": "https://make.powerautomate.com",
    "pa_http_auth": "https://learn.microsoft.com/en-us/power-automate/oauth-authentication",
}

def is_complete(db: DBSession) -> bool:
    r = db.get(AppSetting, "setup_complete")
    return bool(r and r.value == "1")

def get_setting(db: DBSession, key: str, default: str = "") -> str:
    r = db.get(AppSetting, key)
    return r.value if r else default

def set_setting(db: DBSession, key: str, value: str) -> None:
    r = db.get(AppSetting, key)
    if r:
        r.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()

def gate(request: Request, db: DBSession):
    """Returns (user, err_response). None err means allowed."""
    if not is_complete(db):
        user, _ = get_current_user(db, request.cookies.get(COOKIE))
        return user, None
    user, _ = get_current_user(db, request.cookies.get(COOKIE))
    if user and P.is_admin(db, user):
        return user, None
    return None, RedirectResponse("/login", status_code=303)

def new_wtoken(db: DBSession) -> str:
    import secrets
    t = secrets.token_urlsafe(24)
    db.add(OAuthState(state="setup-" + t))
    db.commit()
    return t

def check_wtoken(db: DBSession, tok: str | None) -> bool:
    if not tok:
        return False
    row = db.get(OAuthState, "setup-" + tok)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True

def base_ctx(request, db, step, **kw):
    idx = STEPS.index(step)
    nxt = STEPS[idx + 1] if idx + 1 < len(STEPS) else None
    return ctx(request, db, step=step, steps=STEPS, labels=LABELS, next_step=nxt,
               complete=is_complete(db), links=LINKS, wtoken=new_wtoken(db),
               site_name=site_name(db), **kw)

@router.get("/setup", response_class=HTMLResponse)
def setup_get(request: Request, step: str = "welcome", db: DBSession = Depends(get_db)):
    user, err = gate(request, db)
    if err is not None:
        return err
    if step not in STEPS:
        step = "welcome"
    extra: dict = {}
    if step == "site":
        extra = {"site_name_v": get_setting(db, "site_name", "ScoutWired Portal"),
                 "app_url_v": get_setting(db, "app_url", settings.APP_URL),
                 "timezone_v": get_setting(db, "timezone", "UTC")}
    elif step == "discord_oauth":
        app_url = get_setting(db, "app_url", settings.APP_URL)
        extra = {"redirect_uri": app_url.rstrip("/") + "/login/discord/callback",
                 "oauth_ok": bool(settings.DISCORD_CLIENT_ID)}
    elif step == "discord_server":
        extra = {"guild_v": get_setting(db, "guild_id", settings.DISCORD_GUILD_ID),
                 "bot_ok": bool(settings.DISCORD_BOT_TOKEN)}
    elif step == "roles":
        extra = {"configs": db.query(RoleConfig).all()}
    elif step == "notifications":
        extra = {"pa_ok": bool(settings.POWER_AUTOMATE_URL),
                 "webhooks_ok": {"mod": bool(settings.DISCORD_WEBHOOK_MODERATION),
                                 "public": bool(settings.DISCORD_WEBHOOK_PUBLIC),
                                 "security": bool(settings.DISCORD_WEBHOOK_SECURITY)}}
    elif step == "finish":
        extra = {"summary": finish_summary(db)}
    return templates.TemplateResponse(request, "setup_wizard.html", base_ctx(request, db, step, **extra))

def finish_summary(db: DBSession) -> list[tuple[str, str]]:
    return [
        ("Site name / URL", "configured" if get_setting(db, "site_name") else "missing"),
        ("Discord OAuth (client ID)", "configured" if settings.DISCORD_CLIENT_ID else "NOT configured — set DISCORD_CLIENT_ID/SECRET"),
        ("Discord bot + guild", "configured" if (settings.DISCORD_BOT_TOKEN and (settings.DISCORD_GUILD_ID or get_setting(db, "guild_id"))) else "NOT configured — set DISCORD_BOT_TOKEN/GUILD_ID"),
        ("Role permissions", "configured" if any((c.role_ids or "").strip() for c in db.query(RoleConfig).all()) else "NOT configured — assign at least moderator + admin roles"),
        ("First admin", "set" if db.query(User).filter_by(is_admin_bootstrap=True).count() else "NOT set"),
        ("Power Automate", "configured" if settings.POWER_AUTOMATE_URL else "optional — skipped"),
        ("Discord webhooks", "configured" if any([settings.DISCORD_WEBHOOK_MODERATION, settings.DISCORD_WEBHOOK_PUBLIC, settings.DISCORD_WEBHOOK_SECURITY]) else "optional — skipped"),
    ]

@router.post("/setup/site")
async def setup_site(request: Request, db: DBSession = Depends(get_db)):
    _, err = gate(request, db)
    if err is not None:
        return err
    form = await request.form()
    if not check_wtoken(db, form.get("wtoken")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid setup token. Reload the wizard step.", site_name=site_name(db)), status_code=403)
    name = (form.get("site_name") or "").strip()[:120]
    url = (form.get("app_url") or "").strip().rstrip("/")[:300]
    tz = (form.get("timezone") or "UTC").strip()[:60]
    if not name or not url or not re.match(r"^https?://[^\s/]+", url):
        return templates.TemplateResponse(request, "setup_wizard.html",
            base_ctx(request, db, "site", error="Enter a site name and a valid http(s) app URL.",
                     site_name_v=name, app_url_v=url, timezone_v=tz), status_code=400)
    set_setting(db, "site_name", name)
    set_setting(db, "app_url", url)
    set_setting(db, "timezone", tz)
    user, _ = get_current_user(db, request.cookies.get(COOKIE))
    audit(db, user.id if user else "", "setup_site_saved", "", name[:100])
    return RedirectResponse("/setup?step=discord_oauth", status_code=303)

@router.post("/setup/discord_oauth")
async def setup_oauth(request: Request, db: DBSession = Depends(get_db)):
    _, err = gate(request, db)
    if err is not None:
        return err
    form = await request.form()
    if not check_wtoken(db, form.get("wtoken")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid setup token.", site_name=site_name(db)), status_code=403)
    cid = (form.get("client_id") or "").strip()
    csec = (form.get("client_secret") or "").strip()
    app_url = get_setting(db, "app_url", settings.APP_URL)
    redirect = app_url.rstrip("/") + "/login/discord/callback"
    if not re.fullmatch(r"\d{5,25}", cid):
        return templates.TemplateResponse(request, "setup_wizard.html",
            base_ctx(request, db, "discord_oauth", redirect_uri=redirect,
                     oauth_ok=bool(settings.DISCORD_CLIENT_ID),
                     error="Client ID must be the numeric Application ID from the portal.", ), status_code=400)
    snippet = f"DISCORD_CLIENT_ID={cid}\nDISCORD_CLIENT_SECRET=<paste-secret>\nDISCORD_REDIRECT_URI={redirect}"
    has_secret = bool(csec)
    user, _ = get_current_user(db, request.cookies.get(COOKIE))
    audit(db, user.id if user else "", "setup_oauth_saved", "", f"client_id set: {cid[:4]}…")
    return templates.TemplateResponse(request, "setup_wizard.html",
        base_ctx(request, db, "discord_oauth", redirect_uri=redirect,
                 oauth_ok=bool(settings.DISCORD_CLIENT_ID),
                 flash="Client ID looks valid. Add the lines below to your .env and restart.",
                 env_snippet=snippet if has_secret else snippet.replace("\nDISCORD_CLIENT_SECRET=<paste-secret>", ""),
                 secret_hint=not has_secret))

@router.post("/setup/discord_server")
async def setup_server(request: Request, db: DBSession = Depends(get_db)):
    _, err = gate(request, db)
    if err is not None:
        return err
    form = await request.form()
    if not check_wtoken(db, form.get("wtoken")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid setup token.", site_name=site_name(db)), status_code=403)
    guild = (form.get("guild_id") or "").strip()
    token = (form.get("bot_token") or "").strip()
    if not re.fullmatch(r"\d{5,25}", guild):
        return templates.TemplateResponse(request, "setup_wizard.html",
            base_ctx(request, db, "discord_server", guild_v=guild, bot_ok=bool(settings.DISCORD_BOT_TOKEN),
                     error="Guild ID must be numeric (Developer Mode → right-click server → Copy ID)."), status_code=400)
    set_setting(db, "guild_id", guild)
    roles: list[dict] = []
    test_msg = ""
    if token:
        try:
            r = httpx.get(f"https://discord.com/api/v10/guilds/{guild}/roles",
                          headers={"Authorization": f"Bot {token}"}, timeout=8)
            if r.status_code == 200:
                roles = [{"id": str(x["id"]), "name": x.get("name", "")} for x in r.json()]
                test_msg = f"Connected — {len(roles)} roles found. Copy the IDs you need into the next step."
            elif r.status_code == 401:
                test_msg = "Invalid bot token (401). Check the token in the Bot tab."
            elif r.status_code == 403:
                test_msg = "Bot cannot see this server (403). Invite the bot and enable Server Members Intent."
            elif r.status_code == 404:
                test_msg = "Server not found (404). Check the guild ID."
            else:
                test_msg = f"Discord API returned HTTP {r.status_code}."
        except Exception as e:
            test_msg = f"Connection failed: {e}. Values saved; verify connectivity later."
    snippet = f"DISCORD_GUILD_ID={guild}\nDISCORD_BOT_TOKEN=<paste-token>"
    user, _ = get_current_user(db, request.cookies.get(COOKIE))
    audit(db, user.id if user else "", "setup_guild_saved", "", f"guild {guild}, roles listed: {len(roles)}")
    return templates.TemplateResponse(request, "setup_wizard.html",
        base_ctx(request, db, "discord_server", guild_v=guild, bot_ok=bool(settings.DISCORD_BOT_TOKEN),
                 flash=test_msg or "Guild ID saved. Add the lines below to your .env and restart.",
                 env_snippet=snippet, guild_roles=roles))

@router.post("/setup/roles")
async def setup_roles(request: Request, db: DBSession = Depends(get_db)):
    _, err = gate(request, db)
    if err is not None:
        return err
    form = await request.form()
    if not check_wtoken(db, form.get("wtoken")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid setup token.", site_name=site_name(db)), status_code=403)
    for cat, _, _ in P.CATEGORIES:
        raw = (form.get(f"roles_{cat}") or "").strip()
        ids = [x.strip() for x in raw.split(",") if x.strip()]
        for x in ids:
            if not re.fullmatch(r"\d{5,25}", x):
                return templates.TemplateResponse(request, "setup_wizard.html",
                    base_ctx(request, db, "roles", configs=db.query(RoleConfig).all(),
                             error=f"Invalid role ID {x!r} in {cat} — must be numeric."), status_code=400)
        rc = db.get(RoleConfig, cat)
        if rc:
            rc.role_ids = ",".join(ids)
            rc.enabled = True
    db.commit()
    user, _ = get_current_user(db, request.cookies.get(COOKIE))
    audit(db, user.id if user else "", "setup_roles_saved", "", "")
    return RedirectResponse("/setup?step=first_admin", status_code=303)

@router.post("/setup/first_admin")
async def setup_admin(request: Request, db: DBSession = Depends(get_db)):
    user, err = gate(request, db)
    if err is not None:
        return err
    form = await request.form()
    if not check_wtoken(db, form.get("wtoken")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid setup token.", site_name=site_name(db)), status_code=403)
    did = (form.get("discord_id") or "").strip()
    if not re.fullmatch(r"\d{5,25}", did):
        return templates.TemplateResponse(request, "setup_wizard.html",
            base_ctx(request, db, "first_admin", error="Discord user ID must be numeric (Developer Mode → Copy ID on your profile)."), status_code=400)
    if settings.BOOTSTRAP_TOKEN and not is_complete(db):
        # anonymous fresh-install must present the bootstrap token; logged-in admin is already trusted
        if not (user and P.is_admin(db, user)):
            if (form.get("bootstrap_token") or "") != settings.BOOTSTRAP_TOKEN:
                return templates.TemplateResponse(request, "setup_wizard.html",
                    base_ctx(request, db, "first_admin", error="Wrong bootstrap token. See BOOTSTRAP_TOKEN in your server environment."), status_code=403)
    u = db.query(User).filter_by(discord_id=did).first()
    if not u:
        u = User(kind="discord", display_name=f"admin-{did}", discord_id=did, is_admin_bootstrap=True)
        db.add(u)
    else:
        u.is_admin_bootstrap = True
    db.commit()
    audit(db, user.id if user else "", "setup_first_admin", did, "")
    return templates.TemplateResponse(request, "setup_wizard.html",
        base_ctx(request, db, "first_admin",
                 flash=f"First admin registered for Discord user {did}. That person must now sign in via Discord; then clear is_admin_bootstrap / BOOTSTRAP_TOKEN."))

@router.post("/setup/notifications")
async def setup_notifications(request: Request, db: DBSession = Depends(get_db)):
    _, err = gate(request, db)
    if err is not None:
        return err
    form = await request.form()
    if not check_wtoken(db, form.get("wtoken")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid setup token.", site_name=site_name(db)), status_code=403)
    pa_url = (form.get("pa_url") or "").strip()
    pa_test = ""
    if pa_url:
        if not pa_url.startswith("https://"):
            return templates.TemplateResponse(request, "setup_wizard.html",
                base_ctx(request, db, "notifications", **notif_ctx(db),
                         error="Power Automate URL must be https:// (from the trigger's HTTP URL)."), status_code=400)
        try:
            r = httpx.post(pa_url, json={"event_type": "setup_test", "message": "ScoutWired setup connectivity test"}, timeout=8)
            pa_test = f"Power Automate responded HTTP {r.status_code} (2xx = accepted)." if r.status_code < 400 else f"Power Automate returned HTTP {r.status_code} — check the trigger URL."
        except Exception as e:
            pa_test = f"Could not reach Power Automate: {e}."
    wh = {k: (form.get(k) or "").strip() for k in ("wh_mod", "wh_public", "wh_security")}
    wh_test = ""
    test_wh = wh["wh_mod"]
    if test_wh:
        try:
            r = httpx.post(test_wh, json={"content": "ScoutWired setup test — moderation channel OK."}, timeout=8)
            wh_test = f"Moderation webhook HTTP {r.status_code} (204 = delivered)." if r.status_code < 400 else f"Webhook returned HTTP {r.status_code}."
        except Exception as e:
            wh_test = f"Webhook unreachable: {e}."
    lines = []
    if pa_url:
        lines += ["POWER_AUTOMATE_URL=" + pa_url, "POWER_AUTOMATE_SECRET=<paste-secret>"]
    for key, env in (("wh_mod", "DISCORD_WEBHOOK_MODERATION"), ("wh_public", "DISCORD_WEBHOOK_PUBLIC"), ("wh_security", "DISCORD_WEBHOOK_SECURITY")):
        if wh[key]:
            lines.append(f"{env}=<paste-url>  # re-paste the full URL here")
    user, _ = get_current_user(db, request.cookies.get(COOKIE))
    audit(db, user.id if user else "", "setup_notifications_tested", "", pa_test[:200])
    return templates.TemplateResponse(request, "setup_wizard.html",
        base_ctx(request, db, "notifications", **notif_ctx(db),
                 flash=" ".join(x for x in (pa_test, wh_test) if x) or "Skipped — you can configure notifications later in Admin → Integrations.",
                 env_snippet="\n".join(lines) if lines else None))

def notif_ctx(db):
    return {"pa_ok": bool(settings.POWER_AUTOMATE_URL),
            "webhooks_ok": {"mod": bool(settings.DISCORD_WEBHOOK_MODERATION),
                            "public": bool(settings.DISCORD_WEBHOOK_PUBLIC),
                            "security": bool(settings.DISCORD_WEBHOOK_SECURITY)}}

@router.post("/setup/finish")
async def setup_finish(request: Request, db: DBSession = Depends(get_db)):
    user, err = gate(request, db)
    if err is not None:
        return err
    form = await request.form()
    if not check_wtoken(db, form.get("wtoken")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid setup token.", site_name=site_name(db)), status_code=403)
    set_setting(db, "setup_complete", "1")
    audit(db, user.id if user else "", "setup_completed", "", "")
    return RedirectResponse("/admin" if (user and P.is_admin(db, user)) else "/", status_code=303)
