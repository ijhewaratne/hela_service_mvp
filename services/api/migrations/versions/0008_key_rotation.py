"""Allow re-encryption of append-only history rows during a field-key rotation (Q65).

History tables stay append-only. The one exception is the operator key-rotation command, which runs as the table
owner (the runtime role has no UPDATE grant on these tables at all), sets `hela.key_rotation = on` for its own
transaction, and may then replace encrypted (`*_enc`) values only: every other column must stay identical and an
encrypted value can be neither added nor removed.

Revision ID: 0008_key_rotation
"""
from alembic import op

revision = '0008_key_rotation'
down_revision = '0007_privacy_and_retention'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(r"""
    CREATE OR REPLACE FUNCTION block_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
      o jsonb;
      n jsonb;
    BEGIN
      IF TG_OP = 'UPDATE' AND current_setting('hela.key_rotation', true) = 'on' THEN
        o := to_jsonb(OLD);
        n := to_jsonb(NEW);
        IF (SELECT coalesce(jsonb_object_agg(k, v), '{}') FROM jsonb_each(o) e(k, v) WHERE k NOT LIKE '%\_enc')
             = (SELECT coalesce(jsonb_object_agg(k, v), '{}') FROM jsonb_each(n) e(k, v) WHERE k NOT LIKE '%\_enc')
           AND NOT EXISTS (SELECT 1 FROM jsonb_each(o) e(k, v)
                           WHERE k LIKE '%\_enc' AND (v = 'null'::jsonb) <> ((n -> k) = 'null'::jsonb)) THEN
          RETURN NEW;
        END IF;
      END IF;
      RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
    END $$;
    """)


def downgrade():
    op.execute("""
    CREATE OR REPLACE FUNCTION block_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
    END $$;
    """)
