"""Add principal-private Work scope authority."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_private_work_visibility"
down_revision: str | None = "0020_acquisition_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "private_work_scopes",
        sa.Column("work_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_principal_id"],
            ["principals.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("work_id"),
    )
    op.create_index(
        "ix_private_work_scopes_owner",
        "private_work_scopes",
        ["owner_principal_id", "work_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_private_work_scopes_owner", table_name="private_work_scopes")
    op.drop_table("private_work_scopes")
