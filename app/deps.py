"""Shared request helpers: templates, current user, CSRF, admin guards."""
from fastapi import Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession
from app.database import SessionLocal
from app.auth_sessions import get_current_user, COOKIE
from app import permissions as P

templates = Jinja2Templates(directory="app/templates")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def ctx(request: Request, db: DBSession, **kw):
    raw = request.cookies.get(COOKIE)
    user, sess = get_current_user(db, raw)
    # refresh discord roles opportunistically (once per hour) for sensitive checks
    from app.models import AppSetting as _AS
    _sc = db.get(_AS, "setup_complete")
    return {"request": request, "user": user, "sess": sess,
            "setup_incomplete": not (_sc and _sc.value == "1"),
            "attachments_enabled": __import__("app.config", fromlist=["settings"]).settings.ATTACHMENTS_ENABLED,
            "is_admin": bool(user and P.is_admin(db, user)),
            "csrf_token": sess.csrf_token if sess else "", **kw}

def need_user(request: Request, db: DBSession = Depends(get_db)):
    raw = request.cookies.get(COOKIE)
    user, sess = get_current_user(db, raw)
    if not user:
        raise HTTPException(303, headers={"Location": "/login"})
    return user, sess

def check_csrf(sess, token: str | None) -> bool:
    if not sess or not token:
        return False
    import hmac
    return hmac.compare_digest(sess.csrf_token, token)

def site_name(db) -> str:
    from app.models import AppSetting
    r = db.get(AppSetting, "site_name")
    return r.value if r else "ScoutWired Portal"
