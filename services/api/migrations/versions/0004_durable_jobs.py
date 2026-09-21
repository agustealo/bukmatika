"""Add durable PostgreSQL job leases and cancellable acquisitions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_durable_jobs"
down_revision: str | None = "0003_lawful_acquisition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_JOB_STATES = "'queued','running','completed','failed','cancelled'"
_ACQUISITION_STATES = (
    "'queued','resolving','downloading','verifying','stored','failed','quarantined','cancelled'"
)
_OLD_ACQUISITION_STATES = (
    "'queued','resolving','downloading','verifying','stored','failed','quarantined'"
)


def upgrade() -> None:
    op.drop_constraint("ck_acquisition_status", "acquisitions", type_="check")
    op.create_check_constraint(
        "ck_acquisition_status",
        "acquisitions",
        f"status IN ({_ACQUISITION_STATES})",
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_detail", sa.Text(), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"status IN ({_JOB_STATES})", name="ck_job_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_job_attempt_count"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_job_max_attempts"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_jobs_dedupe_key"),
    )
    op.create_index(
        "ix_jobs_claimable",
        "jobs",
        ["status", "available_at", "lease_expires_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_claimable", table_name="jobs")
    op.drop_table("jobs")
    op.execute(
        "UPDATE acquisitions SET status = 'failed', "
        "error_code = COALESCE(error_code, 'CANCELLED') "
        "WHERE status = 'cancelled'"
    )
    op.drop_constraint("ck_acquisition_status", "acquisitions", type_="check")
    op.create_check_constraint(
        "ck_acquisition_status",
        "acquisitions",
        f"status IN ({_OLD_ACQUISITION_STATES})",
    )
