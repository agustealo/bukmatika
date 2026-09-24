"""Add principal-owned Level 2 delegation consent."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_level2_delegation_consent"
down_revision: str | None = "0016_delegation_attempt_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_delegation_consents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("prior_autonomy_level", sa.Integer(), nullable=False),
        sa.Column(
            "consented_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active','revoked')",
            name="ck_ai_delegation_consent_status",
        ),
        sa.CheckConstraint(
            "scope = 'read_only'",
            name="ck_ai_delegation_consent_scope",
        ),
        sa.CheckConstraint(
            "prior_autonomy_level BETWEEN 0 AND 1",
            name="ck_ai_delegation_consent_prior_level",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "principal_id",
            name="uq_ai_delegation_consent_principal",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_delegation_consents")
