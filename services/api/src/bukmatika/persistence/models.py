from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Principal(Base, TimestampMixin):
    __tablename__ = "principals"
    __table_args__ = (UniqueConstraint("kind", "external_subject", name="uq_principal_subject"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    external_subject: Mapped[str] = mapped_column(String(255), nullable=False)


class Work(Base, TimestampMixin):
    __tablename__ = "works"
    __table_args__ = (
        Index(
            "ix_works_normalized_title_trgm",
            "normalized_title",
            postgresql_using="gin",
            postgresql_ops={"normalized_title": "gin_trgm_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    canonical_title: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_title: Mapped[str] = mapped_column(Text, nullable=False, index=True)


class Edition(Base, TimestampMixin):
    __tablename__ = "editions"
    __table_args__ = (Index("ix_editions_work_publication_year", "work_id", "publication_year"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    work_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(35))
    publication_year: Mapped[int | None] = mapped_column(Integer)
    publisher: Mapped[str | None] = mapped_column(Text)
    edition_statement: Mapped[str | None] = mapped_column(Text)


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("edition_id", "remote_url", name="uq_assets_edition_remote_url"),
        CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="ck_assets_nonnegative_size"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    edition_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("editions.id", ondelete="CASCADE"), nullable=False
    )
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(255))
    remote_url: Mapped[str | None] = mapped_column(Text)
    stored_object_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stored_objects.id", ondelete="SET NULL")
    )
    byte_size: Mapped[int | None] = mapped_column(BigInteger)


class Contributor(Base, TimestampMixin):
    __tablename__ = "contributors"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)


class WorkContributor(Base):
    __tablename__ = "work_contributors"

    work_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), primary_key=True
    )
    contributor_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contributors.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(64), primary_key=True, default="author")


class Identifier(Base, TimestampMixin):
    __tablename__ = "identifiers"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "scheme",
            "normalized_value",
            name="uq_identifier_entity_scheme_value",
        ),
        Index("ix_identifiers_lookup", "scheme", "normalized_value"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    scheme: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str] = mapped_column(Text, nullable=False)


class Subject(Base, TimestampMixin):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_subject_normalized_name"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)


class WorkSubject(Base):
    __tablename__ = "work_subjects"

    work_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), primary_key=True
    )
    subject_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True
    )


class SourceRecord(Base, TimestampMixin):
    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint("provider", "provider_record_id", name="uq_source_provider_record"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_record_id: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)


class SourceRecordLink(Base):
    __tablename__ = "source_record_links"
    __table_args__ = (
        UniqueConstraint(
            "source_record_id",
            "entity_type",
            "entity_id",
            "relationship",
            name="uq_source_record_target",
        ),
    )

    source_record_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_records.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    relationship: Mapped[str] = mapped_column(
        String(64), primary_key=True, default="describes"
    )


class SourceObservation(Base):
    __tablename__ = "source_observations"
    __table_args__ = (
        UniqueConstraint(
            "source_record_id",
            "payload_sha256",
            name="uq_source_observation_payload",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_record_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_records.id", ondelete="CASCADE"), nullable=False
    )
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MetadataAssertion(Base):
    __tablename__ = "metadata_assertions"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_metadata_confidence"),
        Index("ix_metadata_assertions_entity", "entity_type", "entity_id", "field_name"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    source_observation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_observations.id", ondelete="CASCADE"),
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    normalization_method: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RightsEvidenceRecord(Base):
    __tablename__ = "rights_evidence"
    __table_args__ = (
        UniqueConstraint("evidence_sha256", name="uq_rights_evidence_digest"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_rights_evidence_confidence",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_observation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_observations.id", ondelete="SET NULL")
    )
    evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(Text)
    license_uri: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RightsEvidenceSubject(Base):
    __tablename__ = "rights_evidence_subjects"
    __table_args__ = (Index("ix_rights_evidence_subject", "subject_type", "subject_id"),)

    rights_evidence_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("rights_evidence.id", ondelete="CASCADE"),
        primary_key=True,
    )
    subject_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)


class RightsDecision(Base):
    __tablename__ = "rights_decisions"
    __table_args__ = (Index("ix_rights_decisions_subject", "subject_type", "subject_id"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    rights_state: Mapped[str] = mapped_column(String(32), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False, default="US")
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    permissions: Mapped[dict[str, bool]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RightsDecisionEvidence(Base):
    __tablename__ = "rights_decision_evidence"

    rights_decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("rights_decisions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rights_evidence_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("rights_evidence.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class StoredObject(Base):
    __tablename__ = "stored_objects"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_stored_objects_sha256"),
        UniqueConstraint("storage_key", name="uq_stored_objects_storage_key"),
        CheckConstraint("byte_size >= 0", name="ck_stored_objects_nonnegative_size"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Acquisition(Base, TimestampMixin):
    __tablename__ = "acquisitions"
    __table_args__ = (
        UniqueConstraint("asset_id", name="uq_acquisition_asset"),
        CheckConstraint(
            (
                "status IN ('queued','resolving','downloading','verifying',"
                "'stored','failed','quarantined')"
            ),
            name="ck_acquisition_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_acquisition_attempt_count"),
        CheckConstraint(
            "bytes_received IS NULL OR bytes_received >= 0",
            name="ck_acquisition_bytes_received",
        ),
        CheckConstraint("redirect_count >= 0", name="ck_acquisition_redirect_count"),
        Index("ix_acquisitions_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    asset_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    rights_decision_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("rights_decisions.id", ondelete="SET NULL")
    )
    stored_object_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stored_objects.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    remote_url: Mapped[str] = mapped_column(Text, nullable=False)
    expected_format: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_received: Mapped[int | None] = mapped_column(BigInteger)
    redirect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    media_type: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LibraryEntry(Base, TimestampMixin):
    __tablename__ = "library_entries"
    __table_args__ = (
        Index(
            "uq_library_entry_work_only",
            "principal_id",
            "work_id",
            unique=True,
            postgresql_where=text("edition_id IS NULL"),
        ),
        Index(
            "uq_library_entry_edition",
            "principal_id",
            "edition_id",
            unique=True,
            postgresql_where=text("edition_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="CASCADE"), nullable=False
    )
    work_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), nullable=False
    )
    edition_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("editions.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="saved")


class InteractionEvent(Base):
    __tablename__ = "interaction_events"
    __table_args__ = (
        Index("ix_interaction_events_principal_time", "principal_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("principals.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
