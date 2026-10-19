"""Generalize encrypted secret ownership beyond AI provider connections.

Revision ID: 0025_generalized_secrets
Revises: 0024_provider_routing_policy
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_generalized_secrets"
down_revision: str | None = "0024_provider_routing_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "encrypted_secrets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_kind", sa.String(length=64), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slot", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("aad_version", sa.Integer(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "length(btrim(owner_kind)) > 0",
            name="ck_encrypted_secret_owner_kind",
        ),
        sa.CheckConstraint(
            "length(btrim(slot)) > 0",
            name="ck_encrypted_secret_slot",
        ),
        sa.CheckConstraint(
            "aad_version >= 1",
            name="ck_encrypted_secret_aad_version",
        ),
        sa.CheckConstraint(
            "key_version >= 1",
            name="ck_encrypted_secret_key_version",
        ),
        sa.UniqueConstraint(
            "principal_id",
            "owner_kind",
            "owner_id",
            "slot",
            name="uq_encrypted_secret_owner_slot",
        ),
    )
    op.create_index(
        "ix_encrypted_secrets_principal_owner",
        "encrypted_secrets",
        ["principal_id", "owner_kind", "owner_id"],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO encrypted_secrets (
            id,
            principal_id,
            owner_kind,
            owner_id,
            slot,
            ciphertext,
            nonce,
            aad_version,
            key_version,
            created_at,
            updated_at
        )
        SELECT
            id,
            principal_id,
            'ai_provider_connection',
            connection_id,
            'credential',
            ciphertext,
            nonce,
            1,
            key_version,
            created_at,
            updated_at
        FROM ai_provider_credentials
        """
    )
    op.drop_index(
        "ix_ai_provider_credentials_principal_connection",
        table_name="ai_provider_credentials",
    )
    op.drop_table("ai_provider_credentials")


def downgrade() -> None:
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
        sa.UniqueConstraint(
            "connection_id",
            name="uq_ai_provider_credential_connection",
        ),
    )
    op.create_index(
        "ix_ai_provider_credentials_principal_connection",
        "ai_provider_credentials",
        ["principal_id", "connection_id"],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO ai_provider_credentials (
            id,
            principal_id,
            connection_id,
            ciphertext,
            nonce,
            key_version,
            created_at,
            updated_at
        )
        SELECT
            id,
            principal_id,
            owner_id,
            ciphertext,
            nonce,
            key_version,
            created_at,
            updated_at
        FROM encrypted_secrets
        WHERE owner_kind = 'ai_provider_connection'
          AND slot = 'credential'
        """
    )
    op.drop_index(
        "ix_encrypted_secrets_principal_owner",
        table_name="encrypted_secrets",
    )
    op.drop_table("encrypted_secrets")
