"""Fence Reader progress writes by destination-local transaction start time."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_reader_progress_write_order"
down_revision: str | None = "0017_level2_delegation_consent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reading_states",
        sa.Column("position_write_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reading_states", "position_write_started_at")
