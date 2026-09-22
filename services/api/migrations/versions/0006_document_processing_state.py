"""Add durable current processing state for exact stored document sources."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_document_processing_state"
down_revision: str | None = "0005_normalized_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_processing_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("stored_object_id", sa.Uuid(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("processor_name", sa.String(length=64), nullable=False),
        sa.Column("processor_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
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
            "status IN ('processing','completed','requires_ocr','failed')",
            name="ck_document_processing_state_status",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["stored_object_id"],
            ["stored_objects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", name="uq_document_processing_state_asset"),
    )
    op.create_index(
        "ix_document_processing_state_status",
        "document_processing_states",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_processing_state_status",
        table_name="document_processing_states",
    )
    op.drop_table("document_processing_states")
