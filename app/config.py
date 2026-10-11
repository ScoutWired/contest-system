"""Environment-based configuration."""
import os

def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)

def _getint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default

def _getbool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")

class Settings:
    APP_NAME = _get("APP_NAME", "ScoutWired Portal")
    APP_URL = _get("APP_URL", "http://localhost:8000").rstrip("/")
    SECRET_KEY = _get("SECRET_KEY", "dev-secret-change-me-please-32chars!!")
    DATABASE_URL = _get("DATABASE_URL", "sqlite:///./data/portal.db")
    SESSION_COOKIE = _get("SESSION_COOKIE", "sw_session")
    SESSION_DURATION_MIN = _getint("SESSION_DURATION_MIN", 60 * 12)
    SECURE_COOKIES = _getbool("SECURE_COOKIES", False)
    DISCORD_CLIENT_ID = _get("DISCORD_CLIENT_ID", "")
    DISCORD_CLIENT_SECRET = _get("DISCORD_CLIENT_SECRET", "")
    DISCORD_BOT_TOKEN = _get("DISCORD_BOT_TOKEN", "")
    DISCORD_GUILD_ID = _get("DISCORD_GUILD_ID", "")
    DISCORD_REDIRECT_URI = _get("DISCORD_REDIRECT_URI", "")
    BOOTSTRAP_DISCORD_ID = _get("BOOTSTRAP_DISCORD_ID", "")
    BOOTSTRAP_TOKEN = _get("BOOTSTRAP_TOKEN", "")
    POWER_AUTOMATE_URL = _get("POWER_AUTOMATE_URL", "")
    POWER_AUTOMATE_SECRET = _get("POWER_AUTOMATE_SECRET", "")
    DISCORD_WEBHOOK_MODERATION = _get("DISCORD_WEBHOOK_MODERATION", "")
    DISCORD_WEBHOOK_PUBLIC = _get("DISCORD_WEBHOOK_PUBLIC", "")
    DISCORD_WEBHOOK_SECURITY = _get("DISCORD_WEBHOOK_SECURITY", "")
    UPLOAD_DIR = _get("UPLOAD_DIR", "./data/uploads")
    MAX_UPLOAD_MB = _getint("MAX_UPLOAD_MB", 10)
    ALLOWED_UPLOAD_EXTS = _get("ALLOWED_UPLOAD_EXTS", ".pdf,.png,.jpg,.jpeg,.txt,.md,.csv")
    ENV = _get("ENV", "dev")

settings = Settings()
OAUTH_SCOPES = "identify"
