"""Add durable principal-owned action approvals and execution receipts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_action_approvals"
down_revision: str | None = "0010_user_model_local_ai"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("action_decision_id", sa.Uuid(), nullable=False),
        sa.Column("step_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approved','rejected')",
            name="ck_action_approval_decision",
        ),
        sa.ForeignKeyConstraint(
            ["action_decision_id"],
            ["action_decisions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "action_decision_id",
            name="uq_action_approval_action_decision",
        ),
    )
    op.create_index(
        "ix_action_approvals_principal_time",
        "action_approvals",
        ["principal_id", "decided_at"],
    )

    op.create_table(
        "action_execution_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("action_decision_id", sa.Uuid(), nullable=False),
        sa.Column("step_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["action_decision_id"],
            ["action_decisions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "action_decision_id",
            name="uq_action_execution_receipt_action_decision",
        ),
    )
    op.create_index(
        "ix_action_execution_receipts_principal_time",
        "action_execution_receipts",
        ["principal_id", "executed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_action_execution_receipts_principal_time",
        table_name="action_execution_receipts",
    )
    op.drop_table("action_execution_receipts")
    op.drop_index(
        "ix_action_approvals_principal_time",
        table_name="action_approvals",
    )
    op.drop_table("action_approvals")
