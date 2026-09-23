from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class LibraryCollection(Base, TimestampMixin):
    __tablename__ = "library_collections"
    __table_args__ = (
        UniqueConstraint(
            "principal_id",
            "normalized_name",
            name="uq_library_collection_principal_name",
        ),
        Index("ix_library_collections_principal_updated", "principal_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class LibraryCollectionEntry(Base):
    __tablename__ = "library_collection_entries"

    collection_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("library_collections.id", ondelete="CASCADE"),
        primary_key=True,
    )
    library_entry_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("library_entries.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LibraryTag(Base, TimestampMixin):
    __tablename__ = "library_tags"
    __table_args__ = (
        UniqueConstraint(
            "principal_id",
            "normalized_name",
            name="uq_library_tag_principal_name",
        ),
        Index("ix_library_tags_principal_name", "principal_id", "normalized_name"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)


class LibrarySmartShelf(Base, TimestampMixin):
    __tablename__ = "library_smart_shelves"
    __table_args__ = (
        UniqueConstraint(
            "principal_id",
            "normalized_name",
            name="uq_library_smart_shelf_principal_name",
        ),
        CheckConstraint(
            "reading_status IS NOT NULL OR collection_id IS NOT NULL OR tag_id IS NOT NULL",
            name="ck_library_smart_shelf_has_rule",
        ),
        CheckConstraint(
            "reading_status IS NULL OR reading_status IN ('unread','reading','finished')",
            name="ck_library_smart_shelf_reading_status",
        ),
        Index("ix_library_smart_shelves_principal_name", "principal_id", "normalized_name"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    reading_status: Mapped[str | None] = mapped_column(String(32))
    collection_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("library_collections.id", ondelete="CASCADE"),
    )
    tag_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("library_tags.id", ondelete="CASCADE"),
    )


class LibraryEntryTag(Base):
    __tablename__ = "library_entry_tags"

    library_entry_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("library_entries.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("library_tags.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
