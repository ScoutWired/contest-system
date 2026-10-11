"""DB-backed sessions with secure cookies + per-session CSRF tokens."""
from datetime import timedelta
from fastapi import Request
from sqlalchemy.orm import Session as DBSession
from app.models import Session as SessionRow, User, GuestAccount
from app.config import settings
from app.security import gen_token, sha256, utcnow

COOKIE = "sw_session"

def create_session(db: DBSession, user: User, ip: str = "", epoch: int = 0) -> tuple[str, str]:
    raw = gen_token(32)
    csrf = gen_token(24)
    exp = utcnow() + timedelta(minutes=settings.SESSION_DURATION_MIN)
    db.add(SessionRow(id=sha256(raw), user_id=user.id, csrf_token=csrf,
                      expires_at=exp, ip=ip, epoch=episode(epoch, user, db)))
    db.commit()
    return raw, csrf

def episode(epoch: int, user: User, db: DBSession) -> int:
    if user.kind == "guest" and user.guest_id:
        g = db.get(GuestAccount, user.guest_id)
        if g:
            return g.session_epoch
    return 0

def get_session(db: DBSession, raw: str | None) -> SessionRow | None:
    if not raw:
        return None
    s = db.get(SessionRow, sha256(raw))
    if not s or s.revoked:
        return None
    now = utcnow()
    exp = s.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=__import__("datetime").timezone.utc)
    if exp < now:
        return None
    return s

def get_current_user(db: DBSession, raw: str | None) -> tuple[User | None, SessionRow | None]:
    s = get_session(db, raw)
    if not s:
        return None, None
    u = db.get(User, s.user_id)
    if not u:
        return None, None
    # guest revocation/expiry checked live
    if u.kind == "guest" and u.guest_id:
        g = db.get(GuestAccount, u.guest_id)
        if not g or not g.active:
            return None, None
        exp = g.expires_at
        if exp:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=__import__("datetime").timezone.utc)
            if exp < utcnow():
                return None, None
        if s.epoch != g.session_epoch:
            return None, None
    return u, s

def destroy_session(db: DBSession, raw: str | None) -> None:
    s = get_session(db, raw)
    if s:
        s.revoked = True
        db.commit()

def destroy_user_sessions(db: DBSession, user_id: str) -> None:
    for s in db.query(SessionRow).filter(SessionRow.user_id == user_id, SessionRow.revoked == False).all():  # noqa: E712
        s.revoked = True
    db.commit()

def set_cookie(resp, raw: str) -> None:
    resp.set_cookie(COOKIE, raw, httponly=True, samesite="lax",
                    secure=settings.SECURE_COOKIES, path="/",
                    max_age=settings.SESSION_DURATION_MIN * 60)

def clear_cookie(resp) -> None:
    resp.delete_cookie(COOKIE, path="/")
