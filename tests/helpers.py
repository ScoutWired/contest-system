"""Test helpers: make users, sessions, csrf."""
from app.models import User, GuestAccount, Session as SRow, RoleConfig
from app.auth_sessions import create_session
from app.auth_guest import create_guest
from app.security import reset_limiter

def make_discord(db, discord_id="111", roles="", bootstrap=False):
    u = db.query(User).filter_by(discord_id=discord_id).first()
    if not u:
        u = User(kind="discord", display_name=f"user{discord_id}", discord_id=discord_id,
                 roles_cache=roles, is_admin_bootstrap=bootstrap)
        db.add(u); db.commit(); db.refresh(u)
    else:
        u.roles_cache = roles; u.is_admin_bootstrap = bootstrap; db.commit()
    return u

def make_guest(db, note="t"):
    reset_limiter()
    g, u, code = create_guest(db, note=note)
    return g, u, code

def login_as(client, db, user):
    raw, csrf = create_session(db, user, ip="testclient")
    client.cookies.set("sw_session", raw)
    return raw, csrf

def set_role(db, category, role_ids, enabled=True):
    rc = db.get(RoleConfig, category)
    rc.role_ids = role_ids; rc.enabled = enabled; db.commit()

def csrf_of(db, user):
    row = db.query(SRow).filter_by(user_id=user.id, revoked=False).order_by(SRow.created_at.desc()).first()
    return row.csrf_token if row else ""
