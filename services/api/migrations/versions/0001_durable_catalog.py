"""Create durable catalog, provenance, ownership, and rights foundations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_durable_catalog"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp_column(name: str) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return _timestamp_column("created_at"), _timestamp_column("updated_at")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "principals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("external_subject", sa.String(length=255), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "external_subject", name="uq_principal_subject"),
    )
    op.create_table(
        "works",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("canonical_title", sa.Text(), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_works_normalized_title", "works", ["normalized_title"])
    op.create_index(
        "ix_works_normalized_title_trgm",
        "works",
        ["normalized_title"],
        postgresql_using="gin",
        postgresql_ops={"normalized_title": "gin_trgm_ops"},
    )
    op.create_table(
        "editions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("work_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=True),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("publisher", sa.Text(), nullable=True),
        sa.Column("edition_statement", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_editions_work_publication_year",
        "editions",
        ["work_id", "publication_year"],
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("edition_id", sa.Uuid(), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=True),
        sa.Column("remote_url", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "byte_size IS NULL OR byte_size >= 0",
            name="ck_assets_nonnegative_size",
        ),
        sa.ForeignKeyConstraint(["edition_id"], ["editions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256", name="uq_assets_sha256"),
    )
    op.create_table(
        "contributors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contributors_normalized_name", "contributors", ["normalized_name"])
    op.create_table(
        "work_contributors",
        sa.Column("work_id", sa.Uuid(), nullable=False),
        sa.Column("contributor_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["contributor_id"],
            ["contributors.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("work_id", "contributor_id", "role"),
    )
    op.create_table(
        "identifiers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("scheme", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_type",
            "scheme",
            "normalized_value",
            name="uq_identifier_entity_scheme_value",
        ),
    )
    op.create_index("ix_identifiers_lookup", "identifiers", ["scheme", "normalized_value"])
    op.create_table(
        "subjects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_name", name="uq_subject_normalized_name"),
    )
    op.create_table(
        "work_subjects",
        sa.Column("work_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("work_id", "subject_id"),
    )
    op.create_table(
        "source_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_record_id", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_record_id", name="uq_source_provider_record"),
    )
    op.create_table(
        "source_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column(
            "first_observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_records.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_record_id",
            "payload_sha256",
            name="uq_source_observation_payload",
        ),
    )
    op.create_table(
        "metadata_assertions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_observation_id", sa.Uuid(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("normalization_method", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_metadata_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["source_observation_id"],
            ["source_observations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_metadata_assertions_entity",
        "metadata_assertions",
        ["entity_type", "entity_id", "field_name"],
    )
    op.create_table(
        "rights_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_observation_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("evidence_url", sa.Text(), nullable=True),
        sa.Column("license_uri", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_rights_evidence_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["source_observation_id"],
            ["source_observations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "rights_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("rights_state", sa.String(length=32), nullable=False),
        sa.Column("jurisdiction", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rights_decisions_subject",
        "rights_decisions",
        ["subject_type", "subject_id"],
    )
    op.create_table(
        "rights_decision_evidence",
        sa.Column("rights_decision_id", sa.Uuid(), nullable=False),
        sa.Column("rights_evidence_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rights_decision_id"],
            ["rights_decisions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rights_evidence_id"],
            ["rights_evidence.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("rights_decision_id", "rights_evidence_id"),
    )
    op.create_table(
        "library_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("work_id", sa.Uuid(), nullable=False),
        sa.Column("edition_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["edition_id"], ["editions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_library_entry_work_only",
        "library_entries",
        ["principal_id", "work_id"],
        unique=True,
        postgresql_where=sa.text("edition_id IS NULL"),
    )
    op.create_index(
        "uq_library_entry_edition",
        "library_entries",
        ["principal_id", "edition_id"],
        unique=True,
        postgresql_where=sa.text("edition_id IS NOT NULL"),
    )
    op.create_table(
        "interaction_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interaction_events_principal_time",
        "interaction_events",
        ["principal_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_interaction_events_principal_time", table_name="interaction_events")
    op.drop_table("interaction_events")
    op.drop_index("uq_library_entry_edition", table_name="library_entries")
    op.drop_index("uq_library_entry_work_only", table_name="library_entries")
    op.drop_table("library_entries")
    op.drop_table("rights_decision_evidence")
    op.drop_index("ix_rights_decisions_subject", table_name="rights_decisions")
    op.drop_table("rights_decisions")
    op.drop_table("rights_evidence")
    op.drop_index("ix_metadata_assertions_entity", table_name="metadata_assertions")
    op.drop_table("metadata_assertions")
    op.drop_table("source_observations")
    op.drop_table("source_records")
    op.drop_table("work_subjects")
    op.drop_table("subjects")
    op.drop_index("ix_identifiers_lookup", table_name="identifiers")
    op.drop_table("identifiers")
    op.drop_table("work_contributors")
    op.drop_index("ix_contributors_normalized_name", table_name="contributors")
    op.drop_table("contributors")
    op.drop_table("assets")
    op.drop_index("ix_editions_work_publication_year", table_name="editions")
    op.drop_table("editions")
    op.drop_index("ix_works_normalized_title_trgm", table_name="works")
    op.drop_index("ix_works_normalized_title", table_name="works")
    op.drop_table("works")
    op.drop_table("principals")
