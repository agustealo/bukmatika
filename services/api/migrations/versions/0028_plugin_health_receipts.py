"""Persist source-owned connection health receipts.

Revision ID: 0028_plugin_health_receipts
Revises: 0027_plugin_connections
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028_plugin_health_receipts"
down_revision = "0027_plugin_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_health_receipts",
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
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_code", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('verified', 'unavailable')",
            name="ck_plugin_health_receipt_observed_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("plugin_health_receipts")
