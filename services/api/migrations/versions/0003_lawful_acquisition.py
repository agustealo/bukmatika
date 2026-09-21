"""Add exact-asset rights binding and durable acquisition storage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_lawful_acquisition"
down_revision: str | None = "0002_source_record_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ACQUISITION_STATES = (
    "'queued','resolving','downloading','verifying','stored','failed','quarantined'"
)


def upgrade() -> None:
    op.drop_constraint("uq_assets_sha256", "assets", type_="unique")
    op.drop_column("assets", "sha256")

    op.create_table(
        "stored_objects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("byte_size >= 0", name="ck_stored_objects_nonnegative_size"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256", name="uq_stored_objects_sha256"),
        sa.UniqueConstraint("storage_key", name="uq_stored_objects_storage_key"),
    )
    op.add_column("assets", sa.Column("stored_object_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_assets_stored_object",
        "assets",
        "stored_objects",
        ["stored_object_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_assets_stored_object_id", "assets", ["stored_object_id"])

    op.create_table(
        "rights_evidence_subjects",
        sa.Column("rights_evidence_id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rights_evidence_id"],
            ["rights_evidence.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("rights_evidence_id", "subject_type", "subject_id"),
    )
    op.create_index(
        "ix_rights_evidence_subject",
        "rights_evidence_subjects",
        ["subject_type", "subject_id"],
    )

    op.create_table(
        "acquisitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("rights_decision_id", sa.Uuid(), nullable=True),
        sa.Column("stored_object_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("remote_url", sa.Text(), nullable=False),
        sa.Column("expected_format", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes_received", sa.BigInteger(), nullable=True),
        sa.Column("redirect_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("media_type", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
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
        sa.CheckConstraint(
            f"status IN ({_ACQUISITION_STATES})",
            name="ck_acquisition_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_acquisition_attempt_count"),
        sa.CheckConstraint(
            "bytes_received IS NULL OR bytes_received >= 0",
            name="ck_acquisition_bytes_received",
        ),
        sa.CheckConstraint("redirect_count >= 0", name="ck_acquisition_redirect_count"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["rights_decision_id"],
            ["rights_decisions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["stored_object_id"],
            ["stored_objects.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", name="uq_acquisition_asset"),
    )
    op.create_index(
        "ix_acquisitions_status_created",
        "acquisitions",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_acquisitions_status_created", table_name="acquisitions")
    op.drop_table("acquisitions")
    op.drop_index("ix_rights_evidence_subject", table_name="rights_evidence_subjects")
    op.drop_table("rights_evidence_subjects")
    op.drop_index("ix_assets_stored_object_id", table_name="assets")
    op.drop_constraint("fk_assets_stored_object", "assets", type_="foreignkey")
    op.drop_column("assets", "stored_object_id")
    op.drop_table("stored_objects")
    op.add_column("assets", sa.Column("sha256", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_assets_sha256", "assets", ["sha256"])
