"""Store plugin-owned non-secret connection configuration.

Revision ID: 0027_plugin_connections
Revises: 0026_plugin_installations
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027_plugin_connections"
down_revision = "0026_plugin_installations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plugin_installations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plugin_id", sa.String(128), nullable=False),
        sa.Column("mode_id", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("installation_id", name="uq_plugin_connection_installation"),
        sa.CheckConstraint(
            "mode_id ~ '^[a-z0-9][a-z0-9_]{0,63}$'",
            name="ck_plugin_connection_mode",
        ),
    )


def downgrade() -> None:
    op.drop_table("plugin_connections")
