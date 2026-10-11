"""Hashing, tokens, rate limiting, CSRF helpers."""
import hashlib, hmac, secrets, time
from datetime import datetime, timezone

def gen_token(nbytes=32) -> str:
    return secrets.token_urlsafe(nbytes)

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

# ---- guest code hashing (pbkdf2, stdlib only) ----
def hash_code(code: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), 210_000)
    return dk.hex(), salt

def verify_code(code: str, code_hash: str, salt: str) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", code.encode(), salt.encode(), 210_000)
    return hmac.compare_digest(dk.hex(), code_hash)

def gen_guest_username() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "Guest-" + "".join(secrets.choice(alphabet) for _ in range(6))

def gen_guest_code() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(16))

# ---- simple in-memory login rate limiter ----
_attempts: dict[str, list[float]] = {}
_lockouts: dict[str, float] = {}

def rate_key(ip: str, username: str) -> str:
    return f"{ip}|{username.lower()}"

def is_locked(key: str) -> bool:
    until = _lockouts.get(key, 0)
    if time.time() < until:
        return True
    _lockouts.pop(key, None)
    return False

def record_failure(key: str, max_attempts: int = 5, window_s: int = 300, lock_s: int = 600) -> None:
    now = time.time()
    lst = [t for t in _attempts.get(key, []) if now - t < window_s]
    lst.append(now)
    _attempts[key] = lst
    if len(lst) >= max_attempts:
        _lockouts[key] = now + lock_s
        _attempts[key] = []

def record_success(key: str) -> None:
    _attempts.pop(key, None)

def reset_limiter():
    _attempts.clear(); _lockouts.clear()

# ---- HMAC signing for Power Automate ----
def sign_payload(secret: str, body: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()

def utcnow():
    return datetime.now(timezone.utc)
