import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("role IN ('commenter','editor','admin')"), {"schema": "auth"})
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    github_id: Mapped[str] = mapped_column(String(32), unique=True)
    login: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(16), server_default="commenter")
    is_approved: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class LoginSession(Base):
    __tablename__ = "sessions"
    __table_args__ = {"schema": "auth"}
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.accounts.id"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OAuthTransaction(Base):
    __tablename__ = "oauth_transactions"
    __table_args__ = {"schema": "auth"}
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    binding_hash: Mapped[str] = mapped_column(String(64))
    verifier: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PermissionAudit(Base):
    __tablename__ = "permission_audit"
    __table_args__ = {"schema": "auth"}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    actor: Mapped[str] = mapped_column(String(200))
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth.accounts.id"))
    reason: Mapped[str] = mapped_column(Text)
    before: Mapped[dict] = mapped_column(JSONB)
    after: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
