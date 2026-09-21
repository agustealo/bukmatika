"""Link source records and harden discovery persistence identity."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_source_record_links"
down_revision: str | None = "0001_durable_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_record_links",
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("relationship", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_records.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "source_record_id",
            "entity_type",
            "entity_id",
            "relationship",
        ),
        sa.UniqueConstraint(
            "source_record_id",
            "entity_type",
            "entity_id",
            "relationship",
            name="uq_source_record_target",
        ),
    )
    op.create_index(
        "ix_source_record_links_target",
        "source_record_links",
        ["entity_type", "entity_id"],
    )
    op.create_unique_constraint(
        "uq_assets_edition_remote_url",
        "assets",
        ["edition_id", "remote_url"],
    )
    op.add_column(
        "rights_evidence",
        sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_rights_evidence_digest",
        "rights_evidence",
        ["evidence_sha256"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_rights_evidence_digest",
        "rights_evidence",
        type_="unique",
    )
    op.drop_column("rights_evidence", "evidence_sha256")
    op.drop_constraint(
        "uq_assets_edition_remote_url",
        "assets",
        type_="unique",
    )
    op.drop_index("ix_source_record_links_target", table_name="source_record_links")
    op.drop_table("source_record_links")
