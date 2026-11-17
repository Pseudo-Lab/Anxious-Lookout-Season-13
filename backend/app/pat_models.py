"""Independent PAT records; never a runner conversation or bearer plaintext."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base


class Credential(Base):
    __tablename__ = 'credentials'
    __table_args__ = {'schema': 'research'}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID)
    name: Mapped[str] = mapped_column(String(80))
    scope: Mapped[str] = mapped_column(String(8))
    purpose: Mapped[str] = mapped_column(String(40))
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column('issued_at', DateTime(timezone=True), server_default=text('now()'))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rate_window: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rate_count: Mapped[int] = mapped_column(Integer, server_default='0')


class Issuance(Base):
    __tablename__ = 'pat_issuances'
    __table_args__ = {'schema': 'research'}
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    key: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))


class PatAudit(Base):
    __tablename__ = 'pat_audit'
    __table_args__ = {'schema': 'research'}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID)
    operation: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16))
    summary: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
