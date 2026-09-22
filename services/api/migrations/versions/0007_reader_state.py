"""Add durable reading progress and bookmarks."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_reader_state"
down_revision: str | None = "0006_document_processing_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reading_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("library_entry_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("progress_fraction", sa.Float(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=True),
        sa.Column("section_ordinal", sa.Integer(), nullable=True),
        sa.Column("char_offset", sa.Integer(), nullable=True),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('unread','reading','finished')",
            name="ck_reading_state_status",
        ),
        sa.CheckConstraint(
            "progress_fraction >= 0 AND progress_fraction <= 1",
            name="ck_reading_state_progress_fraction",
        ),
        sa.CheckConstraint(
            "section_ordinal IS NULL OR section_ordinal >= 0",
            name="ck_reading_state_section_ordinal",
        ),
        sa.CheckConstraint(
            "char_offset IS NULL OR char_offset >= 0",
            name="ck_reading_state_char_offset",
        ),
        sa.ForeignKeyConstraint(
            ["library_entry_id"],
            ["library_entries.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["document_sections.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "library_entry_id",
            "document_id",
            name="uq_reading_state_library_document",
        ),
    )
    op.create_index(
        "ix_reading_states_library_updated",
        "reading_states",
        ["library_entry_id", "updated_at"],
    )

    op.create_table(
        "bookmarks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reading_state_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("char_offset", sa.Integer(), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
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
        sa.CheckConstraint("char_offset >= 0", name="ck_bookmark_char_offset"),
        sa.ForeignKeyConstraint(
            ["reading_state_id"],
            ["reading_states.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["document_sections.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "reading_state_id",
            "section_id",
            "char_offset",
            name="uq_bookmark_position",
        ),
    )
    op.create_index(
        "ix_bookmarks_state_created",
        "bookmarks",
        ["reading_state_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_bookmarks_state_created", table_name="bookmarks")
    op.drop_table("bookmarks")
    op.drop_index("ix_reading_states_library_updated", table_name="reading_states")
    op.drop_table("reading_states")
