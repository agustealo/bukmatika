"""Add durable delegated-attempt claim leases."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_delegation_attempt_claims"
down_revision: str | None = "0015_ai_delegation_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_delegation_attempts",
        sa.Column("claim_token", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "ai_delegation_attempts",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ai_delegation_attempts",
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_ai_delegation_attempt_claim_consistency",
        "ai_delegation_attempts",
        "(claim_token IS NULL AND claimed_at IS NULL AND claim_expires_at IS NULL) OR "
        "(claim_token IS NOT NULL AND claimed_at IS NOT NULL AND claim_expires_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ai_delegation_attempt_claim_consistency",
        "ai_delegation_attempts",
        type_="check",
    )
    op.drop_column("ai_delegation_attempts", "claim_expires_at")
    op.drop_column("ai_delegation_attempts", "claimed_at")
    op.drop_column("ai_delegation_attempts", "claim_token")
