import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class Item(Base):
    __tablename__ = "items"
    __table_args__ = {"schema": "research"}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID)
    kind: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    content_number: Mapped[int] = mapped_column(Integer, server_default="1")
    archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class ContentVersion(Base):
    __tablename__ = "versions"
    __table_args__ = {"schema": "research"}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    item_id: Mapped[uuid.UUID] = mapped_column(UUID)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID)
    kind: Mapped[str] = mapped_column(String(16))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_kind: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Relation(Base):
    __tablename__ = "relations"
    __table_args__ = {"schema": "research"}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID)
    source_kind: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[uuid.UUID] = mapped_column(UUID)
    target_kind: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    directed: Mapped[bool] = mapped_column(Boolean)
    archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Idempotency(Base):
    __tablename__ = "idempotency"
    __table_args__ = {"schema": "research"}
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    key: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
