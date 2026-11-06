"""Audit chain sealed in commit order (load-test finding).

The 0001 design hashed each row in a BEFORE INSERT trigger under a global advisory lock held to commit. Under
concurrency the id (assigned before the trigger) and the lock order differ, so verification in id order reported
false breaks; the lock also serialised every audited transaction and could deadlock with row locks.

Now: audit_logs stays append-only with no global lock. audit_seal() (SECURITY DEFINER, owner-only writes) appends
committed rows to the append-only audit_chain in the order it seals them. The runtime role can call it but can
never write chain rows itself. All existing rows are sealed by this migration (synthetic pre-production data).

Revision ID: 0005_audit_sealing
"""
from alembic import op

revision = '0005_audit_sealing'
down_revision = '0004_job_priority'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    DROP TRIGGER audit_chain ON audit_logs;
    DROP FUNCTION audit_chain_before_insert();
    DROP FUNCTION audit_verify_chain();
    DROP FUNCTION audit_row_digest(text, audit_logs);
    ALTER TABLE audit_logs DROP COLUMN prev_hash, DROP COLUMN row_hash;

    CREATE TABLE audit_chain (
      position bigserial PRIMARY KEY,
      audit_id bigint NOT NULL UNIQUE REFERENCES audit_logs(id),
      prev_hash text NOT NULL,
      row_hash text NOT NULL,
      sealed_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TRIGGER audit_chain_no_update BEFORE UPDATE OR DELETE ON audit_chain
      FOR EACH ROW EXECUTE FUNCTION audit_block_mutation();
    CREATE TRIGGER audit_chain_no_truncate BEFORE TRUNCATE ON audit_chain
      FOR EACH STATEMENT EXECUTE FUNCTION audit_block_mutation();

    CREATE FUNCTION audit_row_digest(prev text, a audit_logs) RETURNS text
    LANGUAGE sql IMMUTABLE AS $$
      SELECT encode(sha256(convert_to(concat_ws('|', prev, a.id::text,
        to_char(a.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        a.actor_type, a.actor_id, a.action, a.entity_type, a.entity_id, a.details::text, a.correlation_id),
        'UTF8')), 'hex')
    $$;

    -- Seals committed, unsealed rows. One sealer at a time; others return 0 immediately.
    CREATE FUNCTION audit_seal(max_rows int DEFAULT 1000) RETURNS int
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE r audit_logs; prev text; h text; n int := 0; watermark bigint;
    BEGIN
      IF NOT pg_try_advisory_xact_lock(727275) THEN
        RETURN 0;
      END IF;
      SELECT row_hash INTO prev FROM audit_chain ORDER BY position DESC LIMIT 1;
      prev := coalesce(prev, 'GENESIS');
      -- Rows commit out of id order, so look back a window below the highest sealed id.
      SELECT coalesce(max(audit_id), 0) - 10000 INTO watermark FROM audit_chain;
      FOR r IN SELECT a.* FROM audit_logs a
               WHERE a.id > watermark AND NOT EXISTS (SELECT 1 FROM audit_chain c WHERE c.audit_id = a.id)
               ORDER BY a.id LIMIT max_rows LOOP
        h := audit_row_digest(prev, r);
        INSERT INTO audit_chain(audit_id, prev_hash, row_hash) VALUES (r.id, prev, h);
        prev := h;
        n := n + 1;
      END LOOP;
      RETURN n;
    END $$;

    -- First audit id that fails verification: a broken link, a row whose content no longer matches its sealed
    -- hash, or a row left unsealed for more than five minutes. NULL when intact.
    CREATE FUNCTION audit_verify_chain() RETURNS bigint
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE c audit_chain; a audit_logs; expected_prev text := 'GENESIS'; stale bigint;
    BEGIN
      FOR c IN SELECT * FROM audit_chain ORDER BY position LOOP
        SELECT * INTO a FROM audit_logs WHERE id = c.audit_id;
        IF c.prev_hash <> expected_prev OR c.row_hash <> audit_row_digest(c.prev_hash, a) THEN
          RETURN c.audit_id;
        END IF;
        expected_prev := c.row_hash;
      END LOOP;
      SELECT min(a2.id) INTO stale FROM audit_logs a2
       WHERE a2.occurred_at < now() - interval '5 minutes'
         AND NOT EXISTS (SELECT 1 FROM audit_chain c2 WHERE c2.audit_id = a2.id);
      RETURN stale;
    END $$;

    REVOKE ALL ON FUNCTION audit_seal(int) FROM PUBLIC;
    REVOKE ALL ON FUNCTION audit_verify_chain() FROM PUBLIC;
    """)
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hela_app') THEN
        GRANT SELECT ON audit_chain TO hela_app;
        GRANT EXECUTE ON FUNCTION audit_seal(int), audit_verify_chain() TO hela_app;
      END IF;
    END $$;
    SELECT audit_seal(1000000);
    """)


def downgrade():
    raise RuntimeError('0005 is not reversible: the per-row chain columns were replaced by audit_chain')
