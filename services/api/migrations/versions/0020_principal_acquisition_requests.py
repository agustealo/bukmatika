"""Add principal-scoped acquisition policy and request intent."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_acquisition_requests"
down_revision: str | None = "0019_reader_progress_write_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "acquisition_policies",
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_mode", sa.String(length=32), nullable=False),
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
            "approval_mode IN ('always_ask','auto_eligible')",
            name="ck_acquisition_policy_approval_mode",
        ),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("principal_id"),
    )
    op.create_table(
        "acquisition_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("acquisition_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approval_mode", sa.String(length=32), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
            "approval_mode IN ('always_ask','auto_eligible')",
            name="ck_acquisition_request_approval_mode",
        ),
        sa.ForeignKeyConstraint(["acquisition_id"], ["acquisitions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["principal_id"], ["principals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "principal_id",
            "asset_id",
            name="uq_acquisition_request_principal_asset",
        ),
    )
    op.create_index(
        "ix_acquisition_requests_asset_active",
        "acquisition_requests",
        ["asset_id", "cancelled_at", "approved_at"],
        unique=False,
    )
    op.create_index(
        "ix_acquisition_requests_principal_updated",
        "acquisition_requests",
        ["principal_id", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_acquisition_requests_principal_updated",
        table_name="acquisition_requests",
    )
    op.drop_index(
        "ix_acquisition_requests_asset_active",
        table_name="acquisition_requests",
    )
    op.drop_table("acquisition_requests")
    op.drop_table("acquisition_policies")
