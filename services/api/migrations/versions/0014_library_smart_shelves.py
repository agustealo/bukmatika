"""Add principal-owned deterministic smart shelf rules."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_library_smart_shelves"
down_revision: str | None = "0013_library_organization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_smart_shelves",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reading_status", sa.String(length=32), nullable=True),
        sa.Column("collection_id", sa.Uuid(), nullable=True),
        sa.Column("tag_id", sa.Uuid(), nullable=True),
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
            "reading_status IS NOT NULL OR collection_id IS NOT NULL OR tag_id IS NOT NULL",
            name="ck_library_smart_shelf_has_rule",
        ),
        sa.CheckConstraint(
            "reading_status IS NULL OR reading_status IN ('unread','reading','finished')",
            name="ck_library_smart_shelf_reading_status",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["library_collections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["library_tags.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "principal_id",
            "normalized_name",
            name="uq_library_smart_shelf_principal_name",
        ),
    )
    op.create_index(
        "ix_library_smart_shelves_principal_name",
        "library_smart_shelves",
        ["principal_id", "normalized_name"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_library_smart_shelves_principal_name",
        table_name="library_smart_shelves",
    )
    op.drop_table("library_smart_shelves")
