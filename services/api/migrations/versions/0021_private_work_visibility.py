"""Add principal-private Work visibility authority."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_private_work_visibility"
down_revision: str | None = "0020_acquisition_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "works",
        sa.Column(
            "visibility",
            sa.String(length=16),
            server_default="catalog",
            nullable=False,
        ),
    )
    op.add_column(
        "works",
        sa.Column("owner_principal_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_works_owner_principal_id_principals",
        "works",
        "principals",
        ["owner_principal_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_works_visibility_owner",
        "works",
        "(visibility = 'catalog' AND owner_principal_id IS NULL) OR "
        "(visibility = 'private' AND owner_principal_id IS NOT NULL)",
    )
    op.create_index(
        "ix_works_visibility_owner",
        "works",
        ["visibility", "owner_principal_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_works_visibility_owner", table_name="works")
    op.drop_constraint("ck_works_visibility_owner", "works", type_="check")
    op.drop_constraint(
        "fk_works_owner_principal_id_principals",
        "works",
        type_="foreignkey",
    )
    op.drop_column("works", "owner_principal_id")
    op.drop_column("works", "visibility")
