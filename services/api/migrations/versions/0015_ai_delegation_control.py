"""Add bounded principal-owned AI delegation control state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_ai_delegation_control"
down_revision: str | None = "0014_library_smart_shelves"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_delegations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("selected_step_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("delegation_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("max_runtime_seconds", sa.Integer(), nullable=False),
        sa.Column("max_retries_per_step", sa.Integer(), nullable=False),
        sa.Column("max_total_attempts", sa.Integer(), nullable=False),
        sa.Column("attempts_used", sa.Integer(), nullable=False),
        sa.Column("current_step_index", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
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
            "status IN ('proposed','approved','rejected','running','stop_requested',"
            "'stopped','completed','failed','cancelled')",
            name="ck_ai_delegation_status",
        ),
        sa.CheckConstraint(
            "max_runtime_seconds BETWEEN 30 AND 3600",
            name="ck_ai_delegation_runtime_budget",
        ),
        sa.CheckConstraint(
            "max_retries_per_step BETWEEN 0 AND 3",
            name="ck_ai_delegation_retry_budget",
        ),
        sa.CheckConstraint(
            "max_total_attempts BETWEEN 1 AND 48",
            name="ck_ai_delegation_attempt_budget",
        ),
        sa.CheckConstraint("attempts_used >= 0", name="ck_ai_delegation_attempts_used"),
        sa.CheckConstraint("current_step_index >= 0", name="ck_ai_delegation_step_index"),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_delegations_principal_status",
        "ai_delegations",
        ["principal_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_ai_delegations_plan",
        "ai_delegations",
        ["plan_id", "created_at"],
    )

    op.create_table(
        "ai_delegation_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("delegation_id", sa.Uuid(), nullable=False),
        sa.Column("delegation_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approved','rejected')",
            name="ck_ai_delegation_approval_decision",
        ),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["delegation_id"],
            ["ai_delegations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delegation_id", name="uq_ai_delegation_approval_delegation"),
    )
    op.create_index(
        "ix_ai_delegation_approvals_principal_time",
        "ai_delegation_approvals",
        ["principal_id", "decided_at"],
    )

    op.create_table(
        "ai_delegation_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("delegation_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "authorized_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('authorized','completed','failed','cancelled')",
            name="ck_ai_delegation_attempt_status",
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_ai_delegation_attempt_number"),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["delegation_id"],
            ["ai_delegations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "delegation_id",
            "step_id",
            "attempt_number",
            name="uq_ai_delegation_step_attempt",
        ),
    )
    op.create_index(
        "ix_ai_delegation_attempts_delegation_status",
        "ai_delegation_attempts",
        ["delegation_id", "status", "authorized_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_delegation_attempts_delegation_status",
        table_name="ai_delegation_attempts",
    )
    op.drop_table("ai_delegation_attempts")
    op.drop_index(
        "ix_ai_delegation_approvals_principal_time",
        table_name="ai_delegation_approvals",
    )
    op.drop_table("ai_delegation_approvals")
    op.drop_index("ix_ai_delegations_plan", table_name="ai_delegations")
    op.drop_index("ix_ai_delegations_principal_status", table_name="ai_delegations")
    op.drop_table("ai_delegations")
