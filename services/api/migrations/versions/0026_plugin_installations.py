"""Persist principal-scoped trusted plugin installations.

Revision ID: 0026_plugin_installations
Revises: 0025_generalized_secrets
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_plugin_installations"
down_revision = "0025_generalized_secrets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_installations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plugin_id", sa.String(128), nullable=False),
        sa.Column("installed_version", sa.String(64), nullable=False),
        sa.Column("connection_schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("principal_id", "plugin_id", name="uq_plugin_installation_principal_plugin"),
        sa.CheckConstraint("status IN ('enabled', 'disabled')", name="ck_plugin_installation_status"),
        sa.CheckConstraint("connection_schema_version > 0", name="ck_plugin_installation_schema_version"),
    )
    op.create_index(
        "ix_plugin_installations_principal_status",
        "plugin_installations",
        ["principal_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_plugin_installations_principal_status", table_name="plugin_installations")
    op.drop_table("plugin_installations")
