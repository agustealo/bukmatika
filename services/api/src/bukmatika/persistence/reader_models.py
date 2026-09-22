from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class ReadingState(Base, TimestampMixin):
    __tablename__ = "reading_states"
    __table_args__ = (
        UniqueConstraint(
            "library_entry_id",
            "document_id",
            name="uq_reading_state_library_document",
        ),
        CheckConstraint(
            "status IN ('unread','reading','finished')",
            name="ck_reading_state_status",
        ),
        CheckConstraint(
            "progress_fraction >= 0 AND progress_fraction <= 1",
            name="ck_reading_state_progress_fraction",
        ),
        CheckConstraint(
            "section_ordinal IS NULL OR section_ordinal >= 0",
            name="ck_reading_state_section_ordinal",
        ),
        CheckConstraint(
            "char_offset IS NULL OR char_offset >= 0",
            name="ck_reading_state_char_offset",
        ),
        Index("ix_reading_states_library_updated", "library_entry_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    library_entry_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("library_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unread")
    progress_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    section_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document_sections.id", ondelete="SET NULL")
    )
    section_ordinal: Mapped[int | None] = mapped_column(Integer)
    char_offset: Mapped[int | None] = mapped_column(Integer)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Bookmark(Base, TimestampMixin):
    __tablename__ = "bookmarks"
    __table_args__ = (
        UniqueConstraint(
            "reading_state_id",
            "section_id",
            "char_offset",
            name="uq_bookmark_position",
        ),
        CheckConstraint("char_offset >= 0", name="ck_bookmark_char_offset"),
        Index("ix_bookmarks_state_created", "reading_state_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    reading_state_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("reading_states.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_sections.id", ondelete="CASCADE"),
        nullable=False,
    )
    char_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    label: Mapped[str | None] = mapped_column(Text)
