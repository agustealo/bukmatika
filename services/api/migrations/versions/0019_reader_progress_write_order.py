"""Fence Reader progress writes by destination-local transaction start time."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_reader_progress_write_order"
down_revision: str | None = "0018_delegation_attempt_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TRIGGER_NAME = "trg_reading_state_write_order"
_FUNCTION_NAME = "bukmatika_fence_reading_state_write_order"


def upgrade() -> None:
    op.add_column(
        "reading_states",
        sa.Column("position_write_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        f"""
        CREATE FUNCTION {_FUNCTION_NAME}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            write_started_at timestamptz := transaction_timestamp();
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.position_write_started_at IS NOT NULL
               AND write_started_at < OLD.position_write_started_at THEN
                RETURN OLD;
            END IF;

            NEW.position_write_started_at := write_started_at;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER_NAME}
        BEFORE INSERT OR UPDATE ON reading_states
        FOR EACH ROW
        EXECUTE FUNCTION {_FUNCTION_NAME}()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON reading_states")
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION_NAME}()")
    op.drop_column("reading_states", "position_write_started_at")
