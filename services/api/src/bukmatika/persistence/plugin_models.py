from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class PluginInstallation(Base, TimestampMixin):
    """Principal-owned installation state, independent of plugin executable code."""

    __tablename__ = "plugin_installations"
    __table_args__ = (
        UniqueConstraint("principal_id", "plugin_id", name="uq_plugin_installation_owner_plugin"),
        CheckConstraint(
            "status IN ('enabled','disabled')",
            name="ck_plugin_installation_status",
        ),
        Index("ix_plugin_installations_owner_status", "principal_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    installed_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="disabled")
