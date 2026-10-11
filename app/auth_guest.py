"""Guest credential generation and verification."""
from datetime import datetime, timezone
from sqlalchemy.orm import Session as DBSession
from app.models import GuestAccount, User
from app.security import gen_guest_username, gen_guest_code, hash_code, verify_code

def create_guest(db: DBSession, note: str = "", expires_at=None) -> tuple[GuestAccount, User, str]:
    for _ in range(20):
        username = gen_guest_username()
        if not db.query(GuestAccount).filter_by(username=username).first():
            break
    else:
        raise RuntimeError("could not generate unique guest username")
    code = gen_guest_code()
    ch, salt = hash_code(code)
    g = GuestAccount(username=username, code_hash=ch, code_salt=salt, note=note, expires_at=expires_at)
    db.add(g); db.flush()
    u = User(kind="guest", display_name=username, guest_id=g.id)
    db.add(u); db.commit()
    return g, u, code

def verify_guest(db: DBSession, username: str, code: str) -> GuestAccount | None:
    g = db.query(GuestAccount).filter(GuestAccount.username == username.strip()).first()
    if not g or not g.active:
        return None
    if g.expires_at:
        exp = g.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            return None
    if g.locked_until:
        lu = g.locked_until
        if lu.tzinfo is None:
            lu = lu.replace(tzinfo=timezone.utc)
        if lu < datetime.now(timezone.utc):
            g.locked_until = None; g.failed_attempts = 0; db.commit()
        else:
            return None
    if not verify_code(code, g.code_hash, g.code_salt):
        return None
    return g

def regenerate_code(db: DBSession, g: GuestAccount) -> str:
    from app.security import gen_guest_code, hash_code
    code = gen_guest_code()
    ch, salt = hash_code(code)
    g.code_hash = ch; g.code_salt = salt
    g.session_epoch += 1  # invalidate existing sessions
    db.commit()
    return code

def revoke_guest(db: DBSession, g: GuestAccount) -> None:
    g.active = False
    g.session_epoch += 1
    db.commit()
