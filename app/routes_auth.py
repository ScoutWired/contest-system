import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession
from app.deps import templates, get_db, ctx, site_name, check_csrf
from app.models import OAuthState, User, GuestAccount
from app.config import settings
from app.security import rate_key, is_locked, record_failure, record_success, reset_limiter
from app.auth_sessions import create_session, destroy_session, set_cookie, clear_cookie, COOKIE, get_current_user
from app.auth_discord import oauth_url, exchange_code, fetch_member_roles
from app.auth_guest import verify_guest
from app.services_audit import audit

router = APIRouter()

@router.get("/login", response_class=HTMLResponse)
def login(request: Request, db: DBSession = Depends(get_db)):
    return templates.TemplateResponse(request, "login.html", ctx(request, db, site_name=site_name(db)))

@router.get("/login/guest", response_class=HTMLResponse)
def guest_login_form(request: Request, db: DBSession = Depends(get_db)):
    return templates.TemplateResponse(request, "guest_login.html", ctx(request, db, site_name=site_name(db)))

@router.post("/login/guest")
def guest_login(request: Request, username: str = Form(""), code: str = Form(""), db: DBSession = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    key = rate_key(ip, username)
    if is_locked(key):
        audit(db, "", "guest_login_locked", username, f"ip={ip}")
        return templates.TemplateResponse(request, "guest_login.html", ctx(request, db, error="Too many attempts. Try again later.", site_name=site_name(db)), status_code=429)
    g = verify_guest(db, username, code)
    if not g:
        record_failure(key)
        # also bump per-account counter for lockout against guessing
        acc = db.query(GuestAccount).filter(GuestAccount.username == username.strip()).first()
        if acc:
            acc.failed_attempts += 1
            if acc.failed_attempts >= 10:
                from datetime import timedelta
                acc.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
            db.commit()
        audit(db, "", "guest_login_failed", username, f"ip={ip}")
        return templates.TemplateResponse(request, "guest_login.html", ctx(request, db, error="Invalid username or access code.", site_name=site_name(db)), status_code=401)
    record_success(key)
    g.failed_attempts = 0; g.locked_until = None
    g.last_login = datetime.now(timezone.utc)
    u = db.query(User).filter_by(guest_id=g.id).first()
    if not u:
        u = User(kind="guest", display_name=g.username, guest_id=g.id)
        db.add(u); db.flush()
    u.last_login = datetime.now(timezone.utc)
    db.commit()
    raw, _ = create_session(db, u, ip=ip)
    audit(db, u.id, "guest_login", g.username, f"ip={ip}")
    resp = RedirectResponse("/", status_code=303)
    set_cookie(resp, raw)
    return resp

@router.get("/login/discord")
def discord_login(request: Request, db: DBSession = Depends(get_db)):
    if not settings.DISCORD_CLIENT_ID:
        return templates.TemplateResponse(request, "login.html", ctx(request, db, error="Discord login is not configured.", site_name=site_name(db)), status_code=503)
    state = secrets.token_urlsafe(24)
    db.add(OAuthState(state=state)); db.commit()
    return RedirectResponse(oauth_url(state), status_code=303)

@router.get("/login/discord/callback")
def discord_callback(request: Request, code: str = "", state: str = "", db: DBSession = Depends(get_db)):
    row = db.get(OAuthState, state) if state else None
    if not row or not code:
        audit(db, "", "oauth_state_invalid", "", "")
        return templates.TemplateResponse(request, "login.html", ctx(request, db, error="Login failed. Please try again.", site_name=site_name(db)), status_code=400)
    db.delete(row); db.commit()
    info = exchange_code(code)
    if not info:
        return templates.TemplateResponse(request, "login.html", ctx(request, db, error="Discord verification failed.", site_name=site_name(db)), status_code=502)
    roles = fetch_member_roles(info["discord_id"])
    if roles is None:
        # fail closed: cannot verify guild membership
        audit(db, "", "discord_unverifiable", info["discord_id"], "")
        return templates.TemplateResponse(request, "login.html", ctx(request, db, error="Could not verify guild membership. Access denied.", site_name=site_name(db)), status_code=503)
    if not roles and settings.DISCORD_GUILD_ID:
        # not a member (empty set) — deny unless no guild configured
        audit(db, "", "discord_not_member", info["discord_id"], "")
        return templates.TemplateResponse(request, "login.html", ctx(request, db, error="You are not a member of the required Discord server.", site_name=site_name(db)), status_code=403)
    u = db.query(User).filter_by(discord_id=info["discord_id"]).first()
    if not u:
        u = User(kind="discord", display_name=info.get("username", ""), discord_id=info["discord_id"])
        db.add(u); db.flush()
    u.display_name = info.get("username", u.display_name)
    u.roles_cache = ",".join(sorted(roles))
    u.roles_updated_at = datetime.now(timezone.utc)
    u.last_login = datetime.now(timezone.utc)
    db.commit()
    raw, _ = create_session(db, u, ip=request.client.host if request.client else "")
    audit(db, u.id, "discord_login", info["discord_id"], "")
    resp = RedirectResponse("/", status_code=303)
    set_cookie(resp, raw)
    return resp

@router.get("/logout")
def logout_get(request: Request, db: DBSession = Depends(get_db)):
    destroy_session(db, request.cookies.get(COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    clear_cookie(resp)
    return resp

@router.post("/logout")
async def logout_post(request: Request, db: DBSession = Depends(get_db)):
    form = await request.form()
    _, sess = get_current_user(db, request.cookies.get(COOKIE))
    if not check_csrf(sess, form.get("csrf_token")):
        return templates.TemplateResponse(request, "error.html", ctx(request, db, code=403, message="Invalid session token.", site_name=site_name(db)), status_code=403)
    user, _ = get_current_user(db, request.cookies.get(COOKIE))
    if user:
        audit(db, user.id, "logout", "", "")
    destroy_session(db, request.cookies.get(COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    clear_cookie(resp)
    return resp
