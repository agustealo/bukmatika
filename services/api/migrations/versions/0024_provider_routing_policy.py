"""Add provider-neutral AI routing policy state.

Revision ID: 0024_provider_routing_policy
Revises: 0023_provider_credentials
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_provider_routing_policy"
down_revision = "0023_provider_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_routing_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_model_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "model_selection_mode",
            sa.String(length=32),
            nullable=False,
            server_default="installation_default",
        ),
        sa.Column(
            "cloud_egress_policy",
            sa.String(length=32),
            nullable=False,
            server_default="local_only",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("principal_id", name="uq_ai_routing_policy_principal"),
        sa.UniqueConstraint("user_model_id", name="uq_ai_routing_policy_user_model"),
        sa.CheckConstraint(
            "model_selection_mode IN ('installation_default','profile')",
            name="ck_ai_routing_policy_selection",
        ),
        sa.CheckConstraint(
            "cloud_egress_policy IN ('local_only','public_only','private_context')",
            name="ck_ai_routing_policy_egress",
        ),
    )
    op.create_index(
        "ix_ai_routing_policies_principal",
        "ai_routing_policies",
        ["principal_id"],
    )
    op.execute(
        """
        INSERT INTO ai_routing_policies (
            id,
            principal_id,
            user_model_id,
            model_selection_mode,
            cloud_egress_policy,
            created_at,
            updated_at
        )
        SELECT
            gen_random_uuid(),
            principal_id,
            id,
            CASE
                WHEN model_provider_override IS NULL OR model_provider_override = 'none'
                    THEN 'installation_default'
                ELSE 'profile'
            END,
            'local_only',
            now(),
            now()
        FROM user_models
        """
    )


def downgrade() -> None:
    op.drop_index("ix_ai_routing_policies_principal", table_name="ai_routing_policies")
    op.drop_table("ai_routing_policies")
