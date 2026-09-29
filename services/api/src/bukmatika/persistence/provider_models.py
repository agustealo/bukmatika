from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class AIProviderConnection(Base, TimestampMixin):
    __tablename__ = "ai_provider_connections"
    __table_args__ = (
        CheckConstraint(
            "routing_type IN ('local','cloud')",
            name="ck_ai_provider_connection_routing",
        ),
        CheckConstraint(
            "status IN ('enabled','disabled')",
            name="ck_ai_provider_connection_status",
        ),
        UniqueConstraint("id", "principal_id", name="uq_ai_provider_connection_id_principal"),
        Index(
            "ix_ai_provider_connections_principal_provider",
            "principal_id",
            "provider_id",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    routing_type: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    credential_reference: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="enabled")


class AIProviderCredential(Base, TimestampMixin):
    __tablename__ = "ai_provider_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["connection_id", "principal_id"],
            ["ai_provider_connections.id", "ai_provider_connections.principal_id"],
            ondelete="CASCADE",
            name="fk_ai_provider_credential_connection_owner",
        ),
        UniqueConstraint("connection_id", name="uq_ai_provider_credential_connection"),
        Index(
            "ix_ai_provider_credentials_principal_connection",
            "principal_id",
            "connection_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AIModelAssignment(Base, TimestampMixin):
    __tablename__ = "ai_model_assignments"
    __table_args__ = (
        CheckConstraint("length(btrim(role)) > 0", name="ck_ai_model_assignment_role"),
        CheckConstraint("length(btrim(model_id)) > 0", name="ck_ai_model_assignment_model"),
        CheckConstraint("priority >= 0", name="ck_ai_model_assignment_priority"),
        ForeignKeyConstraint(
            ["connection_id", "principal_id"],
            ["ai_provider_connections.id", "ai_provider_connections.principal_id"],
            ondelete="CASCADE",
            name="fk_ai_model_assignment_connection_owner",
        ),
        UniqueConstraint(
            "principal_id",
            "role",
            "priority",
            name="uq_ai_model_assignment_principal_role_priority",
        ),
        Index(
            "ix_ai_model_assignments_principal_role",
            "principal_id",
            "role",
            "enabled",
            "priority",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
