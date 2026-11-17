"""SQLAlchemy models."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


class Role(enum.StrEnum):
    ADMIN = "admin"
    USER = "user"


class DownloadState(enum.StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    INSPECTING = "inspecting"
    WAITING = "waiting"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: States in which a job occupies a download slot.
RUNNING_STATES = (
    DownloadState.INSPECTING,
    DownloadState.DOWNLOADING,
    DownloadState.VERIFYING,
)
#: States that are still "in the queue" from the user's point of view.
OPEN_STATES = (
    DownloadState.PENDING,
    DownloadState.QUEUED,
    DownloadState.WAITING,
    DownloadState.PAUSED,
    *RUNNING_STATES,
)
FINISHED_STATES = (DownloadState.COMPLETED, DownloadState.FAILED, DownloadState.CANCELLED)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(16), default=Role.USER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    theme: Mapped[str] = mapped_column(String(16), default="")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    quota: Mapped[UserQuota | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN.value


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # SHA-256 of the opaque cookie value; the raw token is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(256), default="")

    user: Mapped[User] = relationship(back_populates="sessions")


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(16), default=Role.USER.value)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    used_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class UserQuota(Base):
    """Per-user limits. ``0`` always means "unlimited"."""

    __tablename__ = "user_quotas"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    max_concurrent: Mapped[int] = mapped_column(Integer, default=0)
    max_storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    max_file_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    daily_downloads: Mapped[int] = mapped_column(Integer, default=0)
    bandwidth_bps: Mapped[int] = mapped_column(BigInteger, default=0)
    max_queued: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="quota")


class Download(Base):
    __tablename__ = "downloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    filename: Mapped[str] = mapped_column(String(255), default="")
    state: Mapped[str] = mapped_column(String(16), default=DownloadState.PENDING.value, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    position: Mapped[int] = mapped_column(Integer, default=0)
    # "private" (the owner's folder) or "shared".
    destination: Mapped[str] = mapped_column(String(16), default="private")
    folder: Mapped[str] = mapped_column(String(512), default="")
    total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    downloaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    speed_bps: Mapped[float] = mapped_column(Float, default=0.0)
    avg_speed_bps: Mapped[float] = mapped_column(Float, default=0.0)
    active_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    engine: Mapped[str] = mapped_column(String(16), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error_code: Mapped[str] = mapped_column(String(48), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    wait_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    wait_reason: Mapped[str] = mapped_column(String(255), default="")
    final_path: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship()

    __table_args__ = (
        Index("ix_downloads_queue", "state", "priority", "position"),
        Index("ix_downloads_user_state", "user_id", "state"),
    )


class FileRecord(Base):
    """Metadata for files that FETCH placed in storage."""

    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(16), default="private")
    # Scope-owner key: the user id for private files, 0 for shared ones.
    space: Mapped[int] = mapped_column(Integer, default=0)
    rel_path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    provider: Mapped[str] = mapped_column(String(32), default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    download_id: Mapped[int | None] = mapped_column(
        ForeignKey("downloads.id", ondelete="SET NULL"), nullable=True
    )
    checksum: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("scope", "space", "rel_path", name="uq_files_location"),
        Index("ix_files_space", "scope", "space"),
    )


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health: Mapped[str] = mapped_column(String(16), default="unknown")
    health_message: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    # Preferred authentication mode: "auto" | "anonymous" | "account".
    auth_mode: Mapped[str] = mapped_column(String(16), default="auto", server_default="auto")

    credential: Mapped[ProviderCredential | None] = relationship(
        back_populates="provider", uselist=False, cascade="all, delete-orphan"
    )


class ProviderCredential(Base):
    __tablename__ = "provider_credentials"

    provider_id: Mapped[str] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"), primary_key=True
    )
    # Fernet-encrypted JSON document. Never returned by the API.
    secret: Mapped[str] = mapped_column(Text)
    # Names of the fields that are set, for display ("username, password").
    fields: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Outcome of the last check of this account: NULL = never checked.
    valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    validation_message: Mapped[str] = mapped_column(Text, default="", server_default="")
    validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    provider: Mapped[Provider] = relationship(back_populates="credential")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    # The user the event belongs to (who may see it); NULL for system events.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Who performed it, when different from ``user_id`` (admin actions).
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(24), index=True)
    action: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(8), default="info")
    message: Mapped[str] = mapped_column(Text, default="")
    target: Mapped[str] = mapped_column(String(255), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[Any] = mapped_column(JSON, default=dict)
