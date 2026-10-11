"""Server-side role verification and access-control policies."""
from sqlalchemy.orm import Session as DBSession
from app.models import User, RoleConfig
from app.config import settings

CATEGORIES = [
    ("admin", "Portal administrators", "Full system administration."),
    ("moderator", "Submission moderators", "Approve, reject, request changes, publish."),
    ("voting_manager", "Voting managers", "Create, open, close and archive polls."),
    ("voter", "Eligible voters", "Default Discord voter eligibility."),
    ("contributor", "Submission contributors", "May create submissions."),
    ("results_viewer", "Results viewers", "May view closed poll results."),
    ("guest_manager", "Guest account managers", "May create/revoke guest accounts."),
]

def seed_role_configs(db: DBSession) -> None:
    for cat, label, desc in CATEGORIES:
        if not db.get(RoleConfig, cat):
            db.add(RoleConfig(category=cat, role_ids="", enabled=True, label=label, description=desc))
    db.commit()

def _role_ids(db: DBSession, category: str) -> set[str]:
    rc = db.get(RoleConfig, category)
    if not rc or not rc.enabled:
        return set()
    return {r.strip() for r in (rc.role_ids or "").split(",") if r.strip()}

def user_roles(user: User) -> set[str]:
    return {r.strip() for r in (user.roles_cache or "").split(",") if r.strip()}

def has_perm(db: DBSession, user: User | None, category: str) -> bool:
    """Server-side permission check. Fail closed. Guests never inherit Discord perms."""
    if user is None:
        return False
    if user.kind == "guest":
        return False  # guests never get discord role permissions
    # bootstrap admin: configured discord id is admin until role config grants admin to someone
    if category == "admin" and settings.BOOTSTRAP_DISCORD_ID and user.discord_id == settings.BOOTSTRAP_DISCORD_ID:
        return True
    if getattr(user, "is_admin_bootstrap", False) and category == "admin":
        return True
    allowed = _role_ids(db, category)
    if not allowed:
        return False
    # live role check uses cached roles refreshed at login; sensitive actions refresh via discord API
    return bool(user_roles(user) & allowed)

def is_admin(db, user) -> bool:
    return has_perm(db, user, "admin")

def can_moderate(db, user) -> bool:
    return has_perm(db, user, "admin") or has_perm(db, user, "moderator")

def can_manage_votes(db, user) -> bool:
    return has_perm(db, user, "admin") or has_perm(db, user, "voting_manager")

def can_manage_guests(db, user) -> bool:
    return has_perm(db, user, "admin") or has_perm(db, user, "guest_manager")

def can_contribute(db, user) -> bool:
    if user is None:
        return False
    if user.kind == "guest":
        return True  # authorised (active) guests may submit; active checked elsewhere
    if has_perm(db, user, "admin") or has_perm(db, user, "moderator") or has_perm(db, user, "contributor"):
        return True
    # default: any authenticated discord guild member may submit
    return bool(user.discord_id)
