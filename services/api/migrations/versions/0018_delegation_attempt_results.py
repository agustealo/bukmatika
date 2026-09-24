"""Add durable delegated attempt result receipts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_delegation_attempt_results"
down_revision: str | None = "0017_level2_delegation_consent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_delegation_attempt_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("delegation_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("receipt", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["delegation_id"],
            ["ai_delegations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["ai_delegation_attempts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "attempt_id",
            name="uq_ai_delegation_attempt_result_attempt",
        ),
    )
    op.create_index(
        "ix_ai_delegation_attempt_results_principal_time",
        "ai_delegation_attempt_results",
        ["principal_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_delegation_attempt_results_delegation",
        "ai_delegation_attempt_results",
        ["delegation_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_delegation_attempt_results_delegation",
        table_name="ai_delegation_attempt_results",
    )
    op.drop_index(
        "ix_ai_delegation_attempt_results_principal_time",
        table_name="ai_delegation_attempt_results",
    )
    op.drop_table("ai_delegation_attempt_results")
