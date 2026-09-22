"""Add durable local-first principal sessions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_principal_sessions"
down_revision: str | None = "0007_reader_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "principal_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_sha256", name="uq_principal_session_token"),
    )
    op.create_index(
        "ix_principal_sessions_principal_expires",
        "principal_sessions",
        ["principal_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_principal_sessions_principal_expires",
        table_name="principal_sessions",
    )
    op.drop_table("principal_sessions")
