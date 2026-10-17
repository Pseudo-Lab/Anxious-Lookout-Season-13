"""Posts are a distinct content layer; no changes to item kinds or relations."""
import uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base


class Post(Base):
    __tablename__ = 'posts'
    __table_args__ = {'schema': 'research'}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text('gen_random_uuid()'))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    legacy_import: Mapped[bool] = mapped_column(Boolean, server_default='false')
    slug: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, server_default='1')
    content_number: Mapped[int] = mapped_column(Integer, server_default='1')
    archived: Mapped[bool] = mapped_column(Boolean, server_default='false')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))


class PostVersion(Base):
    __tablename__ = 'post_versions'
    __table_args__ = {'schema': 'research'}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text('gen_random_uuid()'))
    post_id: Mapped[uuid.UUID] = mapped_column(UUID)
    number: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))


class PostDraft(Base):
    __tablename__ = 'post_drafts'
    __table_args__ = {'schema': 'research'}
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, server_default=text('gen_random_uuid()'))
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID)
    post_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    revision: Mapped[int] = mapped_column(Integer, server_default='1')
    base_post_version: Mapped[int | None] = mapped_column(Integer)
    base_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    payload: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(16), server_default='active')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text('now()'))
