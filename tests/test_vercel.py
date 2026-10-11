"""Vercel deployment support tests."""
from tests.helpers import make_guest
from app.security import reset_limiter

def test_handler_exposes_app():
    import api.index as h
    assert hasattr(h, "app") and hasattr(h, "handler")
    assert "/health" in h.app.openapi()["paths"]

def test_postgres_scheme_normalised():
    from app.database import normalize_url
    assert normalize_url("postgres://u:p@host/db") == "postgresql+psycopg2://u:p@host/db"
    assert normalize_url("postgresql://u@host/db") == "postgresql://u@host/db"
    assert normalize_url("sqlite:///./data/x.db").startswith("sqlite")

def test_sqlite_rejected_on_vercel(monkeypatch):
    from app.database import resolve_url
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/x.db")
    try:
        resolve_url()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "Postgres" in str(e)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@host/db")
    assert resolve_url().startswith("postgresql")

def test_attachments_can_be_disabled(client, db, monkeypatch):
    from app import config as cfg
    reset_limiter()
    monkeypatch.setattr(cfg.settings, "ATTACHMENTS_ENABLED", False)
    g, u, code = make_guest(db, "tvercel")
    client.post("/login/guest", data={"username": g.username, "code": code})
    from app.auth_sessions import get_current_user
    _, sess = get_current_user(db, client.cookies.get("sw_session"))
    r = client.get("/submit")
    assert "disabled on this deployment" in r.text
    r = client.post("/submit", data={"csrf_token": sess.csrf_token, "title": "T", "description": "d" * 50},
                    files={"attachment": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 400 and "disabled" in r.text
    client.cookies.clear()
