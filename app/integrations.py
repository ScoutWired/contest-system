"""Discord + Power Automate outbound delivery with dedup + bounded retries."""
import json, hashlib, uuid
import httpx
from datetime import datetime, timezone
from sqlalchemy.orm import Session as DBSession
from app.models import WebhookEvent, DeliveryAttempt
from app.config import settings
from app.security import sign_payload

MAX_TRIES = 3

def _targets() -> dict[str, str]:
    return {
        "power_automate": settings.POWER_AUTOMATE_URL,
        "discord_mod": settings.DISCORD_WEBHOOK_MODERATION,
        "discord_public": settings.DISCORD_WEBHOOK_PUBLIC,
        "discord_security": settings.DISCORD_WEBHOOK_SECURITY,
    }

def queue_event(db: DBSession, event_type: str, dedup_key: str, payload: dict) -> WebhookEvent:
    """Idempotent: same dedup_key never creates a duplicate logical event."""
    existing = db.query(WebhookEvent).filter_by(dedup_key=dedup_key).first()
    if existing:
        return existing
    ev = WebhookEvent(event_id=uuid.uuid4().hex, dedup_key=dedup_key,
                      event_type=event_type, payload=json.dumps(payload))
    db.add(ev)
    for target in _targets():
        db.add(DeliveryAttempt(event_id=ev.event_id, target=target))
    db.commit()
    return ev

def _send_one(url: str, target: str, payload: dict, event_id: str) -> tuple[bool, str]:
    body = json.dumps(payload)
    headers = {"Content-Type": "application/json", "X-SW-Event-Id": event_id}
    if target == "power_automate" and settings.POWER_AUTOMATE_SECRET:
        headers["X-SW-Signature"] = sign_payload(settings.POWER_AUTOMATE_SECRET, body)
    try:
        if target.startswith("discord"):
            content = f"**{payload.get('event_type','event')}** — {payload.get('title','')} ({payload.get('status','')})".strip()[:1800]
            r = httpx.post(url, json={"content": content}, timeout=8)
        else:
            r = httpx.post(url, content=body, headers=headers, timeout=8)
        if 200 <= r.status_code < 300:
            return True, ""
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:300]

def deliver_due(db: DBSession, event_id: str | None = None) -> dict:
    """Deliver pending attempts with bounded retries. Never raises into business logic."""
    from app.database import SessionLocal
    local = db
    q = local.query(DeliveryAttempt).filter_by(status="pending")
    if event_id:
        q = q.filter(DeliveryAttempt.event_id == event_id)
    attempts = q.all()
    urls = _targets()
    out = {"sent": 0, "failed": 0, "skipped": 0}
    for a in attempts:
        if a.tries >= MAX_TRIES:
            a.status = "failed"; out["failed"] += 1; continue
        url = urls.get(a.target, "")
        if not url:
            a.status = "failed"; a.last_error = "no URL configured"; out["skipped"] += 1; continue
        ev = local.get(WebhookEvent, a.event_id)
        try:
            payload = json.loads(ev.payload) if ev else {}
        except Exception:
            payload = {}
        try:
            ok, err = _send_one(url, a.target, payload, a.event_id)
        except Exception as e:
            ok, err = False, str(e)[:300]
        a.tries += 1
        if ok:
            a.status = "sent"; out["sent"] += 1
        else:
            a.last_error = err
            if a.tries >= MAX_TRIES:
                a.status = "failed"
            out["failed"] += 1
    try:
        local.commit()
    except Exception:
        local.rollback()
    return out

def fire_and_forget(event_type: str, dedup_key: str, payload: dict) -> None:
    """Called from request handlers: queue event; attempt delivery without failing the request."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        ev = queue_event(db, event_type, dedup_key, payload)
        try:
            deliver_due(db, ev.event_id)
        except Exception:
            pass
    except Exception:
        try: db.rollback()
        except Exception: pass
    finally:
        db.close()
