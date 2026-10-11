"""All database models."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import (String, Text, Integer, Boolean, DateTime, ForeignKey,
                        UniqueConstraint, Index, JSON)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

def uid(prefix=""):
    return f"{prefix}{uuid.uuid4().hex[:12]}"

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: uuid.uuid4().hex)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # 'discord' | 'guest'
    display_name: Mapped[str] = mapped_column(String(120), default="")
    discord_id: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    guest_id: Mapped[str | None] = mapped_column(String(40), ForeignKey("guest_accounts.id"), nullable=True)
    is_admin_bootstrap: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # cached discord roles (refreshed at login & on sensitive checks)
    roles_cache: Mapped[str] = mapped_column(Text, default="")  # comma-separated
    roles_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class GuestAccount(Base):
    __tablename__ = "guest_accounts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: uuid.uuid4().hex)
    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    code_hash: Mapped[str] = mapped_column(String(300))
    code_salt: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_epoch: Mapped[int] = mapped_column(Integer, default=0)  # bump to invalidate sessions

class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # token hash
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    epoch: Mapped[int] = mapped_column(Integer, default=0)  # guest epoch at creation
    ip: Mapped[str] = mapped_column(String(64), default="")

class RoleConfig(Base):
    __tablename__ = "role_configs"
    category: Mapped[str] = mapped_column(String(64), primary_key=True)
    role_ids: Mapped[str] = mapped_column(Text, default="")  # comma-separated discord role ids
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")

class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80), index=True, default="General")
    summary: Mapped[str] = mapped_column(String(600), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    links: Mapped[str] = mapped_column(Text, default="")
    proposed_options: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="PENDING_REVIEW", index=True)
    submitter_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attachment_path: Mapped[str] = mapped_column(String(300), default="")
    attachment_name: Mapped[str] = mapped_column(String(200), default="")

class SubmissionRevision(Base):
    __tablename__ = "submission_revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(String(40), ForeignKey("submissions.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(String(600), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    edited_by: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class ModerationDecision(Base):
    __tablename__ = "moderation_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(String(40), ForeignKey("submissions.id"), index=True)
    moderator_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))  # APPROVE/REJECT/CHANGES/PUBLISH/ARCHIVE
    reason: Mapped[str] = mapped_column(Text, default="")
    internal_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Poll(Base):
    __tablename__ = "polls"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    submission_id: Mapped[str | None] = mapped_column(String(40), ForeignKey("submissions.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)  # DRAFT/OPEN/CLOSED/ARCHIVED
    eligibility: Mapped[str] = mapped_column(String(20), default="both")  # discord/guests/both
    allow_change: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_abstain: Mapped[bool] = mapped_column(Boolean, default=True)
    hide_until_close: Mapped[bool] = mapped_column(Boolean, default=True)
    opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str] = mapped_column(String(40), default="")  # WINNER:<opt>|TIED|NO_WINNER
    created_by: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    options: Mapped[list] = relationship("PollOption", back_populates="poll", cascade="all, delete-orphan")

class PollOption(Base):
    __tablename__ = "poll_options"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    poll_id: Mapped[str] = mapped_column(String(40), ForeignKey("polls.id"), index=True)
    text: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_abstain: Mapped[bool] = mapped_column(Boolean, default=False)
    poll: Mapped[Poll] = relationship("Poll", back_populates="options")

class PollGuestAllow(Base):
    __tablename__ = "poll_guest_allow"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[str] = mapped_column(String(40), ForeignKey("polls.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    __table_args__ = (UniqueConstraint("poll_id", "user_id", name="uq_poll_guest"),)

class Vote(Base):
    __tablename__ = "votes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[str] = mapped_column(String(40), ForeignKey("polls.id"), index=True)
    voter_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    option_id: Mapped[str] = mapped_column(String(40), ForeignKey("poll_options.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("poll_id", "voter_id", name="uq_poll_voter"),)

class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_audit_created", "created_at"),)

class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), ForeignKey("webhook_events.event_id"), index=True)
    target: Mapped[str] = mapped_column(String(40))  # power_automate|discord_mod|discord_public|discord_security
    url_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/sent/failed
    tries: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")

class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
