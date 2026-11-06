"""Job priority so dispatch and expiry are not queued behind notifications (NFR-03 load finding).

Revision ID: 0004_job_priority
"""
from alembic import op

revision = '0004_job_priority'
down_revision = '0003_booking_engine'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE jobs ADD COLUMN priority int NOT NULL DEFAULT 100;
    DROP INDEX jobs_due;
    CREATE INDEX jobs_due ON jobs (priority, run_at) WHERE status IN ('queued','running');
    """)


def downgrade():
    op.execute("""
    DROP INDEX jobs_due;
    ALTER TABLE jobs DROP COLUMN priority;
    CREATE INDEX jobs_due ON jobs (run_at) WHERE status IN ('queued','running');
    """)
