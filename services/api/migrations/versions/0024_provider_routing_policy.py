"""Add provider-neutral AI routing policy state.

Revision ID: 0024_provider_routing_policy
Revises: 0023_provider_credentials
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_provider_routing_policy"
down_revision = "0023_provider_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_models",
        sa.Column(
            "model_selection_mode",
            sa.String(length=32),
            nullable=False,
            server_default="installation_default",
        ),
    )
    op.add_column(
        "user_models",
        sa.Column(
            "cloud_egress_policy",
            sa.String(length=32),
            nullable=False,
            server_default="local_only",
        ),
    )
    op.create_check_constraint(
        "ck_user_model_model_selection_mode",
        "user_models",
        "model_selection_mode IN ('installation_default','profile')",
    )
    op.create_check_constraint(
        "ck_user_model_cloud_egress_policy",
        "user_models",
        "cloud_egress_policy IN ('local_only','public_only','private_context')",
    )

    op.execute(
        """
        UPDATE user_models
        SET model_selection_mode = CASE
            WHEN model_provider_override IS NULL OR model_provider_override = 'none'
                THEN 'installation_default'
            ELSE 'profile'
        END,
        cloud_egress_policy = 'local_only'
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_user_model_cloud_egress_policy",
        "user_models",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_model_model_selection_mode",
        "user_models",
        type_="check",
    )
    op.drop_column("user_models", "cloud_egress_policy")
    op.drop_column("user_models", "model_selection_mode")
