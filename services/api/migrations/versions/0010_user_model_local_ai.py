"""Add principal-owned local AI model selection overrides."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_user_model_local_ai"
down_revision: str | None = "0009_personalization_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_models",
        sa.Column("model_provider_override", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "user_models",
        sa.Column("model_name_override", sa.String(length=255), nullable=True),
    )
    op.create_check_constraint(
        "ck_user_model_model_provider_override",
        "user_models",
        "model_provider_override IS NULL OR model_provider_override IN ('none','ollama')",
    )
    op.create_check_constraint(
        "ck_user_model_model_override_consistency",
        "user_models",
        "(model_provider_override IS NULL AND model_name_override IS NULL) OR "
        "(model_provider_override = 'none' AND model_name_override IS NULL) OR "
        "(model_provider_override = 'ollama' AND model_name_override IS NOT NULL "
        "AND length(btrim(model_name_override)) > 0)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_user_model_model_override_consistency",
        "user_models",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_model_model_provider_override",
        "user_models",
        type_="check",
    )
    op.drop_column("user_models", "model_name_override")
    op.drop_column("user_models", "model_provider_override")
