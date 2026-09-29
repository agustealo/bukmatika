"""Add encrypted principal-owned provider credentials."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_provider_credentials"
down_revision: str | None = "0022_provider_neutral_ai"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["connection_id", "principal_id"],
            ["ai_provider_connections.id", "ai_provider_connections.principal_id"],
            ondelete="CASCADE",
            name="fk_ai_provider_credential_connection_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", name="uq_ai_provider_credential_connection"),
    )
    op.create_index(
        "ix_ai_provider_credentials_principal_connection",
        "ai_provider_credentials",
        ["principal_id", "connection_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_provider_credentials_principal_connection",
        table_name="ai_provider_credentials",
    )
    op.drop_table("ai_provider_credentials")
