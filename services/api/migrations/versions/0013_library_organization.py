"""Add principal-owned library collections and tags."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_library_organization"
down_revision: str | None = "0012_reader_highlights"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_collections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "principal_id",
            "normalized_name",
            name="uq_library_collection_principal_name",
        ),
    )
    op.create_index(
        "ix_library_collections_principal_updated",
        "library_collections",
        ["principal_id", "updated_at"],
    )

    op.create_table(
        "library_collection_entries",
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("library_entry_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["library_collections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["library_entry_id"],
            ["library_entries.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("collection_id", "library_entry_id"),
    )

    op.create_table(
        "library_tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "principal_id",
            "normalized_name",
            name="uq_library_tag_principal_name",
        ),
    )
    op.create_index(
        "ix_library_tags_principal_name",
        "library_tags",
        ["principal_id", "normalized_name"],
    )

    op.create_table(
        "library_entry_tags",
        sa.Column("library_entry_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["library_entry_id"],
            ["library_entries.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tag_id"], ["library_tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("library_entry_id", "tag_id"),
    )


def downgrade() -> None:
    op.drop_table("library_entry_tags")
    op.drop_index("ix_library_tags_principal_name", table_name="library_tags")
    op.drop_table("library_tags")
    op.drop_table("library_collection_entries")
    op.drop_index(
        "ix_library_collections_principal_updated",
        table_name="library_collections",
    )
    op.drop_table("library_collections")
