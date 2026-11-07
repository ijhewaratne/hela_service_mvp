"""Operational heartbeats for monitoring (§17): worker liveness and backup/restore checks.

Revision ID: 0009_ops_heartbeats
"""
from alembic import op

revision = '0009_ops_heartbeats'
down_revision = '0008_key_rotation'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE ops_heartbeats (
      name text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]{0,39}$'),
      last_success_at timestamptz,
      last_failure_at timestamptz,
      detail text CHECK (char_length(detail) <= 200),
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX audit_logs_action_time ON audit_logs (action, occurred_at);
    CREATE INDEX sensitive_access_logs_time ON sensitive_access_logs (created_at);
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hela_app') THEN
        GRANT SELECT, INSERT, UPDATE ON ops_heartbeats TO hela_app;
      END IF;
    END $$;
    """)


def downgrade():
    op.execute("""
    DROP INDEX IF EXISTS sensitive_access_logs_time;
    DROP INDEX IF EXISTS audit_logs_action_time;
    DROP TABLE ops_heartbeats;
    """)
