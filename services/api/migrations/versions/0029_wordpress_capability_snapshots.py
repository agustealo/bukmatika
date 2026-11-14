"""Persist authenticated WordPress capability observations.

Revision ID: 0029_wp_capability_snapshots
Revises: 0028_plugin_health_receipts
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0029_wp_capability_snapshots"
down_revision = "0028_plugin_health_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wordpress_capability_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plugin_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("evidence_code", sa.String(64), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edit_posts", sa.Boolean(), nullable=False),
        sa.Column("publish_posts", sa.Boolean(), nullable=False),
        sa.Column("upload_files", sa.Boolean(), nullable=False),
        sa.Column("assign_categories", sa.Boolean(), nullable=False),
        sa.Column("manage_categories", sa.Boolean(), nullable=False),
        sa.Column("edit_categories", sa.Boolean(), nullable=False),
        sa.Column("assign_post_tags", sa.Boolean(), nullable=False),
        sa.Column("manage_post_tags", sa.Boolean(), nullable=False),
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
            "state IN ('verified', 'unavailable')",
            name="ck_wordpress_capability_snapshot_state",
        ),
    )
    op.create_index(
        "ix_wordpress_capability_snapshot_latest",
        "wordpress_capability_snapshots",
        ["principal_id", "connection_id", "checked_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_wordpress_capability_snapshot_latest",
        table_name="wordpress_capability_snapshots",
    )
    op.drop_table("wordpress_capability_snapshots")
