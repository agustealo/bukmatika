"""Generalize encrypted credentials into a connection-agnostic secret store.

Revision ID: 0025_generalized_secrets
Revises: 0024_provider_routing_policy
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025_generalized_secrets"
down_revision = "0024_provider_routing_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "encrypted_secrets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_kind", sa.String(length=64), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slot", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("aad_scheme", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "length(btrim(owner_kind)) > 0",
            name="ck_encrypted_secret_owner_kind",
        ),
        sa.CheckConstraint(
            "length(btrim(slot)) > 0",
            name="ck_encrypted_secret_slot",
        ),
        sa.CheckConstraint(
            "key_version > 0",
            name="ck_encrypted_secret_key_version",
        ),
        sa.CheckConstraint(
            "length(btrim(aad_scheme)) > 0",
            name="ck_encrypted_secret_aad_scheme",
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
        "ix_encrypted_secrets_owner",
        "encrypted_secrets",
        ["principal_id", "owner_kind", "owner_id"],
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
            key_version,
            aad_scheme,
            created_at,
            updated_at
        )
        SELECT
            id,
            principal_id,
            'ai_provider_connection',
            connection_id,
            'api_key',
            ciphertext,
            nonce,
            key_version,
            'ai_provider_v1',
            created_at,
            updated_at
        FROM ai_provider_credentials
        """
    )
    op.drop_table("ai_provider_credentials")


def downgrade() -> None:
    op.create_table(
        "ai_provider_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "principal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["connection_id", "principal_id"],
            ["ai_provider_connections.id", "ai_provider_connections.principal_id"],
            ondelete="CASCADE",
            name="fk_ai_provider_credential_connection_owner",
        ),
        sa.UniqueConstraint(
            "connection_id",
            name="uq_ai_provider_credential_connection",
        ),
    )
    op.create_index(
        "ix_ai_provider_credentials_principal_connection",
        "ai_provider_credentials",
        ["principal_id", "connection_id"],
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
          AND slot = 'api_key'
          AND aad_scheme = 'ai_provider_v1'
        """
    )
    op.drop_index("ix_encrypted_secrets_owner", table_name="encrypted_secrets")
    op.drop_table("encrypted_secrets")
