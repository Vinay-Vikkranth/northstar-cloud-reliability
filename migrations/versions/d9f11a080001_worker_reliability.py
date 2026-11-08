"""Fenced leases, durable trace carrier and execution observations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "d9f11a080001"
down_revision = "c4a1e2b7d9f0"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("worker_jobs", sa.Column("lease_token", sa.String(36), nullable=True))
    op.add_column("worker_jobs", sa.Column("trace_context", JSONB(), nullable=False, server_default="{}"))
    op.add_column("worker_jobs", sa.Column("last_duration_ms", sa.Float(), nullable=True))
    op.add_column("worker_jobs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))

def downgrade():
    for name in ("finished_at", "last_duration_ms", "trace_context", "lease_token"):
        op.drop_column("worker_jobs", name)
