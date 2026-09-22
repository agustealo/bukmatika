"""Add canonical adaptive AI personalization foundations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_personalization_foundation"
down_revision: str | None = "0008_principal_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_models",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column(
            "ai_enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "learning_enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "autonomy_level",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
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
            "autonomy_level BETWEEN 0 AND 3",
            name="ck_user_model_autonomy_level",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("principal_id", name="uq_user_model_principal"),
    )

    op.create_table(
        "goals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=64),
            server_default="research",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "constraints",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
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
            "status IN ('active','paused','completed','cancelled')",
            name="ck_goal_status",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_goals_principal_status",
        "goals",
        ["principal_id", "status", "updated_at"],
    )

    op.create_table(
        "preference_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_model_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "scope_type",
            sa.String(length=32),
            server_default="global",
            nullable=False,
        ),
        sa.Column(
            "scope_value",
            sa.String(length=255),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "evidence_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "first_observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_reinforced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("decay_half_life_days", sa.Float(), nullable=True),
        sa.Column(
            "influence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text(
                "'{\"ranking\": true, \"presentation\": true, \"automation\": false}'::jsonb"
            ),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            "source IN ('explicit','inferred')",
            name="ck_preference_claim_source",
        ),
        sa.CheckConstraint(
            "status IN ('active','contradicted','superseded','expired','deleted')",
            name="ck_preference_claim_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_preference_claim_confidence",
        ),
        sa.CheckConstraint(
            "evidence_count >= 0",
            name="ck_preference_claim_evidence_count",
        ),
        sa.CheckConstraint(
            "decay_half_life_days IS NULL OR decay_half_life_days > 0",
            name="ck_preference_claim_decay",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_model_id"],
            ["user_models.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_preference_claims_principal_status",
        "preference_claims",
        ["principal_id", "status", "updated_at"],
    )
    op.create_index(
        "uq_preference_claim_active_scope",
        "preference_claims",
        ["principal_id", "key", "scope_type", "scope_value"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("goal_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="proposed",
            nullable=False,
        ),
        sa.Column("user_request", sa.Text(), nullable=False),
        sa.Column("planner_version", sa.String(length=64), nullable=False),
        sa.Column(
            "steps",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "context_manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
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
            "status IN ('proposed','approved','running','completed','failed','cancelled')",
            name="ck_plan_status",
        ),
        sa.ForeignKeyConstraint(
            ["goal_id"],
            ["goals.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_plans_principal_status",
        "plans",
        ["principal_id", "status", "updated_at"],
    )

    op.create_table(
        "action_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("capability", sa.String(length=128), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('allow','allow_with_notification','require_approval','deny')",
            name="ck_action_decision_value",
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
            "plan_id",
            "step_id",
            name="uq_action_decision_plan_step",
        ),
    )
    op.create_index(
        "ix_action_decisions_plan",
        "action_decisions",
        ["plan_id", "evaluated_at"],
    )

    op.create_table(
        "outcome_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column("action_decision_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('helped','accepted','ignored','rejected','undone','corrected','failed')",
            name="ck_outcome_event_value",
        ),
        sa.ForeignKeyConstraint(
            ["action_decision_id"],
            ["action_decisions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plans.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outcome_events_principal_time",
        "outcome_events",
        ["principal_id", "occurred_at"],
    )

    op.create_table(
        "preference_claim_evidence",
        sa.Column("preference_claim_id", sa.Uuid(), nullable=False),
        sa.Column("interaction_event_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["interaction_event_id"],
            ["interaction_events.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["preference_claim_id"],
            ["preference_claims.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "preference_claim_id",
            "interaction_event_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("preference_claim_evidence")
    op.drop_index(
        "ix_outcome_events_principal_time",
        table_name="outcome_events",
    )
    op.drop_table("outcome_events")
    op.drop_index(
        "ix_action_decisions_plan",
        table_name="action_decisions",
    )
    op.drop_table("action_decisions")
    op.drop_index(
        "ix_plans_principal_status",
        table_name="plans",
    )
    op.drop_table("plans")
    op.drop_index(
        "uq_preference_claim_active_scope",
        table_name="preference_claims",
    )
    op.drop_index(
        "ix_preference_claims_principal_status",
        table_name="preference_claims",
    )
    op.drop_table("preference_claims")
    op.drop_index(
        "ix_goals_principal_status",
        table_name="goals",
    )
    op.drop_table("goals")
    op.drop_table("user_models")
