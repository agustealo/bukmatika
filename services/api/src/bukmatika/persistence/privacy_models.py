from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base


class PrivacyErasureObject(Base):
    """Durable storage cleanup intent that survives principal deletion.

    Only canonical storage keys are retained. No principal identity, title, filename, prompt,
    annotation, or other user content is copied into this queue.
    """

    __tablename__ = "privacy_erasure_objects"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_privacy_erasure_objects_storage_key"),
        Index("ix_privacy_erasure_objects_pending", "status", "queued_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["PrivacyErasureObject"]
