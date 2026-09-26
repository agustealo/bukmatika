"""Add durable privacy storage-erasure queue."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_privacy_erasure_queue"
down_revision: str | None = "0020_acquisition_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "privacy_erasure_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_privacy_erasure_objects_storage_key"),
    )
    op.create_index(
        "ix_privacy_erasure_objects_pending",
        "privacy_erasure_objects",
        ["status", "queued_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_privacy_erasure_objects_pending", table_name="privacy_erasure_objects")
    op.drop_table("privacy_erasure_objects")
