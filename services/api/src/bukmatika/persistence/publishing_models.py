from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class Publication(Base, TimestampMixin):
    """Principal-owned canonical publication identity."""

    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("id", "principal_id", name="uq_publications_id_owner"),
        CheckConstraint(
            "state IN ('active','trashed')",
            name="ck_publications_state",
        ),
        Index("ix_publications_owner_updated", "principal_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


class PublicationRevision(Base):
    """Immutable canonical content revision."""

    __tablename__ = "publication_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["publication_id", "principal_id"],
            ["publications.id", "publications.principal_id"],
            ondelete="CASCADE",
            name="fk_publication_revision_owner",
        ),
        UniqueConstraint(
            "publication_id",
            "revision_number",
            name="uq_publication_revision_number",
        ),
        UniqueConstraint(
            "id",
            "publication_id",
            "principal_id",
            name="uq_publication_revision_identity",
        ),
        CheckConstraint("revision_number > 0", name="ck_publication_revision_positive"),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_publication_revision_sha256",
        ),
        Index(
            "ix_publication_revisions_owner_publication",
            "principal_id",
            "publication_id",
            "revision_number",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class PublicationDestination(Base, TimestampMixin):
    """Stable historical target independent of credential lifetime."""

    __tablename__ = "publication_destinations"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "principal_id",
            name="uq_publication_destination_id_owner",
        ),
        UniqueConstraint(
            "principal_id",
            "plugin_id",
            "destination_kind",
            "external_locator",
            name="uq_publication_destination_locator",
        ),
        CheckConstraint(
            "state IN ('active','retired')",
            name="ck_publication_destination_state",
        ),
        CheckConstraint(
            "length(btrim(destination_kind)) > 0",
            name="ck_publication_destination_kind",
        ),
        CheckConstraint(
            "length(btrim(external_locator)) > 0",
            name="ck_publication_destination_locator",
        ),
        Index(
            "ix_publication_destinations_connection",
            "principal_id",
            "connection_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    )
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    connection_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plugin_connections.id", ondelete="SET NULL"),
    )
    destination_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    external_locator: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


class PublicationDelivery(Base, TimestampMixin):
    """Idempotent intent to deliver one immutable revision to one destination."""

    __tablename__ = "publication_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["publication_id", "principal_id"],
            ["publications.id", "publications.principal_id"],
            ondelete="RESTRICT",
            name="fk_publication_delivery_publication",
        ),
        ForeignKeyConstraint(
            ["revision_id", "publication_id", "principal_id"],
            [
                "publication_revisions.id",
                "publication_revisions.publication_id",
                "publication_revisions.principal_id",
            ],
            ondelete="RESTRICT",
            name="fk_publication_delivery_revision",
        ),
        ForeignKeyConstraint(
            ["destination_id", "principal_id"],
            ["publication_destinations.id", "publication_destinations.principal_id"],
            ondelete="RESTRICT",
            name="fk_publication_delivery_destination",
        ),
        UniqueConstraint(
            "id",
            "principal_id",
            name="uq_publication_delivery_id_owner",
        ),
        UniqueConstraint(
            "principal_id",
            "destination_id",
            "idempotency_key",
            name="uq_publication_delivery_idempotency",
        ),
        CheckConstraint(
            "state IN ('pending','in_flight','succeeded','failed','unknown')",
            name="ck_publication_delivery_state",
        ),
        CheckConstraint(
            "length(btrim(idempotency_key)) > 0",
            name="ck_publication_delivery_idempotency_key",
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_publication_delivery_fingerprint",
        ),
        CheckConstraint(
            "latest_attempt >= 0",
            name="ck_publication_delivery_attempt_count",
        ),
        Index(
            "ix_publication_deliveries_owner_state",
            "principal_id",
            "state",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    publication_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    revision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    destination_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    latest_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remote_resource_id: Mapped[str | None] = mapped_column(Text)
    remote_revision_id: Mapped[str | None] = mapped_column(Text)
    remote_url: Mapped[str | None] = mapped_column(Text)
    remote_status: Mapped[str | None] = mapped_column(String(32))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublicationDeliveryAttempt(Base):
    """Durable attempt state written before network mutation and settled afterward."""

    __tablename__ = "publication_delivery_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["delivery_id", "principal_id"],
            ["publication_deliveries.id", "publication_deliveries.principal_id"],
            ondelete="CASCADE",
            name="fk_publication_delivery_attempt_owner",
        ),
        UniqueConstraint(
            "delivery_id",
            "attempt_number",
            name="uq_publication_delivery_attempt_number",
        ),
        CheckConstraint(
            "attempt_number > 0",
            name="ck_publication_delivery_attempt_positive",
        ),
        CheckConstraint(
            "state IN ('started','succeeded','failed','unknown')",
            name="ck_publication_delivery_attempt_state",
        ),
        CheckConstraint(
            "response_fingerprint IS NULL OR response_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_publication_delivery_attempt_response_sha",
        ),
        Index(
            "ix_publication_delivery_attempts_owner",
            "principal_id",
            "delivery_id",
            "attempt_number",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    delivery_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="started")
    evidence_code: Mapped[str | None] = mapped_column(String(64))
    response_fingerprint: Mapped[str | None] = mapped_column(String(64))
    remote_resource_id: Mapped[str | None] = mapped_column(Text)
    remote_revision_id: Mapped[str | None] = mapped_column(Text)
    remote_url: Mapped[str | None] = mapped_column(Text)
    remote_status: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
