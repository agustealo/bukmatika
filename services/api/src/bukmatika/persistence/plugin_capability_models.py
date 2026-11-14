from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class WordPressCapabilitySnapshot(Base, TimestampMixin):
    """Durable source-owned observation of one authenticated WordPress capability check."""

    __tablename__ = "wordpress_capability_snapshots"
    __table_args__ = (
        CheckConstraint(
            "state IN ('verified', 'unavailable')",
            name="ck_wordpress_capability_snapshot_state",
        ),
        Index(
            "ix_wordpress_capability_snapshot_latest",
            "principal_id",
            "connection_id",
            "checked_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plugin_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_code: Mapped[str] = mapped_column(String(64), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edit_posts: Mapped[bool] = mapped_column(Boolean, nullable=False)
    publish_posts: Mapped[bool] = mapped_column(Boolean, nullable=False)
    upload_files: Mapped[bool] = mapped_column(Boolean, nullable=False)
    assign_categories: Mapped[bool] = mapped_column(Boolean, nullable=False)
    manage_categories: Mapped[bool] = mapped_column(Boolean, nullable=False)
    edit_categories: Mapped[bool] = mapped_column(Boolean, nullable=False)
    assign_post_tags: Mapped[bool] = mapped_column(Boolean, nullable=False)
    manage_post_tags: Mapped[bool] = mapped_column(Boolean, nullable=False)
