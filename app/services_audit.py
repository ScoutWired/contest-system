from sqlalchemy.orm import Session as DBSession
from app.models import AuditEvent

def audit(db: DBSession, actor_id: str, action: str, target: str = "", detail: str = "") -> None:
    db.add(AuditEvent(actor_id=actor_id or "", action=action, target=target, detail=detail[:2000]))
    db.commit()
