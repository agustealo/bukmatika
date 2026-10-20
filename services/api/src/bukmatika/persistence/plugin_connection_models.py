from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class PluginConnection(Base, TimestampMixin):
    """Connection settings contain no secrets; secrets use the encrypted vault."""

    __tablename__ = "plugin_connections"
    __table_args__ = (
        UniqueConstraint("installation_id", name="uq_plugin_connection_installation"),
        CheckConstraint(
            "mode_id ~ '^[a-z0-9][a-z0-9_]{0,63}$'",
            name="ck_plugin_connection_mode",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    installation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plugin_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    mode_id: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False)
    settings: Mapped[dict[str, str | int]] = mapped_column(JSONB, nullable=False)
