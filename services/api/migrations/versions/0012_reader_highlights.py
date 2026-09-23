"""Add durable reader highlights and notes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_reader_highlights"
down_revision: str | None = "0011_action_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "highlights",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reading_state_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.CheckConstraint("char_start >= 0", name="ck_highlight_char_start"),
        sa.CheckConstraint("char_end > char_start", name="ck_highlight_char_range"),
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
            "char_start",
            "char_end",
            name="uq_highlight_range",
        ),
    )
    op.create_index(
        "ix_highlights_state_created",
        "highlights",
        ["reading_state_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_highlights_state_created", table_name="highlights")
    op.drop_table("highlights")
