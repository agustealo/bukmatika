"""Add provider-neutral AI connections and model assignments."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_provider_neutral_ai"
down_revision: str | None = "0021_privacy_erasure_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _deterministic_uuid_sql(seed_sql: str) -> str:
    digest = f"md5({seed_sql})"
    return (
        "(substr(" + digest + ", 1, 8) || '-' || "
        "substr(" + digest + ", 9, 4) || '-' || "
        "substr(" + digest + ", 13, 4) || '-' || "
        "substr(" + digest + ", 17, 4) || '-' || "
        "substr(" + digest + ", 21, 12))::uuid"
    )


def upgrade() -> None:
    op.create_table(
        "ai_provider_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("routing_type", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("credential_reference", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
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
            "routing_type IN ('local','cloud')",
            name="ck_ai_provider_connection_routing",
        ),
        sa.CheckConstraint(
            "status IN ('enabled','disabled')",
            name="ck_ai_provider_connection_status",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "principal_id",
            name="uq_ai_provider_connection_id_principal",
        ),
    )
    op.create_index(
        "ix_ai_provider_connections_principal_provider",
        "ai_provider_connections",
        ["principal_id", "provider_id", "status"],
        unique=False,
    )

    op.create_table(
        "ai_model_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
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
            "length(btrim(role)) > 0",
            name="ck_ai_model_assignment_role",
        ),
        sa.CheckConstraint(
            "length(btrim(model_id)) > 0",
            name="ck_ai_model_assignment_model",
        ),
        sa.CheckConstraint(
            "priority >= 0",
            name="ck_ai_model_assignment_priority",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id", "principal_id"],
            ["ai_provider_connections.id", "ai_provider_connections.principal_id"],
            ondelete="CASCADE",
            name="fk_ai_model_assignment_connection_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "principal_id",
            "role",
            "priority",
            name="uq_ai_model_assignment_principal_role_priority",
        ),
    )
    op.create_index(
        "ix_ai_model_assignments_principal_role",
        "ai_model_assignments",
        ["principal_id", "role", "enabled", "priority"],
        unique=False,
    )

    connection_id = _deterministic_uuid_sql(
        "principal_id::text || ':provider:ollama'"
    )
    assignment_id = _deterministic_uuid_sql(
        "principal_id::text || ':role:primary:ollama:' || model_name_override"
    )

    op.execute(
        sa.text(
            f"""
            INSERT INTO ai_provider_connections (
                id,
                principal_id,
                provider_id,
                routing_type,
                display_name,
                credential_reference,
                status,
                created_at,
                updated_at
            )
            SELECT
                {connection_id},
                principal_id,
                'ollama',
                'local',
                'Ollama',
                NULL,
                'enabled',
                created_at,
                updated_at
            FROM user_models
            WHERE model_provider_override = 'ollama'
              AND model_name_override IS NOT NULL
              AND length(btrim(model_name_override)) > 0
            ON CONFLICT (id) DO NOTHING
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            INSERT INTO ai_model_assignments (
                id,
                principal_id,
                connection_id,
                role,
                model_id,
                priority,
                enabled,
                last_validated_at,
                created_at,
                updated_at
            )
            SELECT
                {assignment_id},
                principal_id,
                {connection_id},
                'primary',
                model_name_override,
                0,
                TRUE,
                NULL,
                created_at,
                updated_at
            FROM user_models
            WHERE model_provider_override = 'ollama'
              AND model_name_override IS NOT NULL
              AND length(btrim(model_name_override)) > 0
            ON CONFLICT (principal_id, role, priority) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_ai_model_assignments_principal_role", table_name="ai_model_assignments")
    op.drop_table("ai_model_assignments")
    op.drop_index(
        "ix_ai_provider_connections_principal_provider",
        table_name="ai_provider_connections",
    )
    op.drop_table("ai_provider_connections")
