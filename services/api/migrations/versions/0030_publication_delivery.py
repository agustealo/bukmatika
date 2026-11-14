"""Add canonical publication, destination, and delivery history.

Revision ID: 0030_publication_delivery
Revises: 0029_wp_capability_snapshots
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030_publication_delivery"
down_revision = "0029_wp_capability_snapshots"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("id", "principal_id", name="uq_publications_id_owner"),
        sa.CheckConstraint(
            "state IN ('active','trashed')",
            name="ck_publications_state",
        ),
    )
    op.create_index(
        "ix_publications_owner_updated",
        "publications",
        ["principal_id", "updated_at"],
    )

    op.create_table(
        "publication_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text()),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["publication_id", "principal_id"],
            ["publications.id", "publications.principal_id"],
            ondelete="CASCADE",
            name="fk_publication_revision_owner",
        ),
        sa.UniqueConstraint(
            "publication_id",
            "revision_number",
            name="uq_publication_revision_number",
        ),
        sa.UniqueConstraint(
            "id",
            "publication_id",
            "principal_id",
            name="uq_publication_revision_identity",
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_publication_revision_positive",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_publication_revision_sha256",
        ),
    )
    op.create_index(
        "ix_publication_revisions_owner_publication",
        "publication_revisions",
        ["principal_id", "publication_id", "revision_number"],
    )

    op.create_table(
        "publication_destinations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plugin_id", sa.String(128), nullable=False),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plugin_connections.id", ondelete="SET NULL"),
        ),
        sa.Column("destination_kind", sa.String(64), nullable=False),
        sa.Column("external_locator", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "id",
            "principal_id",
            name="uq_publication_destination_id_owner",
        ),
        sa.UniqueConstraint(
            "principal_id",
            "plugin_id",
            "destination_kind",
            "external_locator",
            name="uq_publication_destination_locator",
        ),
        sa.CheckConstraint(
            "state IN ('active','retired')",
            name="ck_publication_destination_state",
        ),
        sa.CheckConstraint(
            "length(btrim(destination_kind)) > 0",
            name="ck_publication_destination_kind",
        ),
        sa.CheckConstraint(
            "length(btrim(external_locator)) > 0",
            name="ck_publication_destination_locator",
        ),
    )
    op.create_index(
        "ix_publication_destinations_connection",
        "publication_destinations",
        ["principal_id", "connection_id"],
    )

    op.create_table(
        "publication_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("latest_attempt", sa.Integer(), nullable=False),
        sa.Column("remote_resource_id", sa.Text()),
        sa.Column("remote_revision_id", sa.Text()),
        sa.Column("remote_url", sa.Text()),
        sa.Column("remote_status", sa.String(32)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["publication_id", "principal_id"],
            ["publications.id", "publications.principal_id"],
            ondelete="RESTRICT",
            name="fk_publication_delivery_publication",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id", "publication_id", "principal_id"],
            [
                "publication_revisions.id",
                "publication_revisions.publication_id",
                "publication_revisions.principal_id",
            ],
            ondelete="RESTRICT",
            name="fk_publication_delivery_revision",
        ),
        sa.ForeignKeyConstraint(
            ["destination_id", "principal_id"],
            ["publication_destinations.id", "publication_destinations.principal_id"],
            ondelete="RESTRICT",
            name="fk_publication_delivery_destination",
        ),
        sa.UniqueConstraint(
            "id",
            "principal_id",
            name="uq_publication_delivery_id_owner",
        ),
        sa.UniqueConstraint(
            "principal_id",
            "destination_id",
            "idempotency_key",
            name="uq_publication_delivery_idempotency",
        ),
        sa.CheckConstraint(
            "state IN ('pending','in_flight','succeeded','failed','unknown')",
            name="ck_publication_delivery_state",
        ),
        sa.CheckConstraint(
            "length(btrim(idempotency_key)) > 0",
            name="ck_publication_delivery_idempotency_key",
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_publication_delivery_fingerprint",
        ),
        sa.CheckConstraint(
            "latest_attempt >= 0",
            name="ck_publication_delivery_attempt_count",
        ),
    )
    op.create_index(
        "ix_publication_deliveries_owner_state",
        "publication_deliveries",
        ["principal_id", "state", "updated_at"],
    )

    op.create_table(
        "publication_delivery_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("evidence_code", sa.String(64)),
        sa.Column("response_fingerprint", sa.String(64)),
        sa.Column("remote_resource_id", sa.Text()),
        sa.Column("remote_revision_id", sa.Text()),
        sa.Column("remote_url", sa.Text()),
        sa.Column("remote_status", sa.String(32)),
        sa.Column("error_code", sa.String(64)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["delivery_id", "principal_id"],
            ["publication_deliveries.id", "publication_deliveries.principal_id"],
            ondelete="CASCADE",
            name="fk_publication_delivery_attempt_owner",
        ),
        sa.UniqueConstraint(
            "delivery_id",
            "attempt_number",
            name="uq_publication_delivery_attempt_number",
        ),
        sa.CheckConstraint(
            "attempt_number > 0",
            name="ck_publication_delivery_attempt_positive",
        ),
        sa.CheckConstraint(
            "state IN ('started','succeeded','failed','unknown')",
            name="ck_publication_delivery_attempt_state",
        ),
        sa.CheckConstraint(
            "response_fingerprint IS NULL OR response_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_publication_delivery_attempt_response_sha",
        ),
    )
    op.create_index(
        "ix_publication_delivery_attempts_owner",
        "publication_delivery_attempts",
        ["principal_id", "delivery_id", "attempt_number"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_publication_delivery_attempts_owner",
        table_name="publication_delivery_attempts",
    )
    op.drop_table("publication_delivery_attempts")
    op.drop_index(
        "ix_publication_deliveries_owner_state",
        table_name="publication_deliveries",
    )
    op.drop_table("publication_deliveries")
    op.drop_index(
        "ix_publication_destinations_connection",
        table_name="publication_destinations",
    )
    op.drop_table("publication_destinations")
    op.drop_index(
        "ix_publication_revisions_owner_publication",
        table_name="publication_revisions",
    )
    op.drop_table("publication_revisions")
    op.drop_index("ix_publications_owner_updated", table_name="publications")
    op.drop_table("publications")
