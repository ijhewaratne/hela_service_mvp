"""M01 foundation: identity, sessions, admin RBAC, notices, regions, maker/checker, tamper-evident audit.

Revision ID: 0001_foundation
"""
from alembic import op

revision = '0001_foundation'
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = 'hela_app'

PERMISSIONS = [
    ('verification.review', 'Review provider evidence and decide category approvals'),
    ('dispatch.manage', 'Dispatch, retry and reassign requests'),
    ('incident.respond', 'Triage and act on incidents'),
    ('finance.reconcile', 'Reconcile offline settlements and disputes'),
    ('audit.read', 'Read redacted audit records'),
    ('policy.manage', 'Propose and approve region/category/policy changes (super-admin only)'),
    ('admin.manage', 'Grant and revoke administrator roles (super-admin only)'),
]
ROLES = {
    'super_admin': ['verification.review', 'dispatch.manage', 'incident.respond', 'finance.reconcile',
                    'audit.read', 'policy.manage', 'admin.manage'],
    'operations_admin': ['dispatch.manage', 'audit.read'],
    'verification_reviewer': ['verification.review'],
    'incident_responder': ['incident.respond'],
    'finance_reconciler': ['finance.reconcile'],
    'auditor': ['audit.read'],
}


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS postgis')
    op.execute('CREATE EXTENSION IF NOT EXISTS btree_gist')
    op.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto')

    op.execute("""
    CREATE TABLE users (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      phone_lookup text NOT NULL UNIQUE,
      phone_enc bytea NOT NULL,
      phone_last3 text NOT NULL CHECK (phone_last3 ~ '^[0-9]{3}$'),
      display_name text CHECK (char_length(display_name) <= 80),
      preferred_language text NOT NULL DEFAULT 'en' CHECK (preferred_language IN ('si','ta','en')),
      account_state text NOT NULL DEFAULT 'active' CHECK (account_state IN ('active','suspended','closed')),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE user_personas (
      user_id uuid NOT NULL REFERENCES users(id),
      persona text NOT NULL CHECK (persona IN ('customer','provider')),
      state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','suspended','closed')),
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (user_id, persona)
    );

    CREATE TABLE otp_challenges (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      phone_lookup text NOT NULL,
      purpose text NOT NULL CHECK (purpose IN ('login')),
      code_hash text NOT NULL,
      attempts int NOT NULL DEFAULT 0,
      max_attempts int NOT NULL,
      expires_at timestamptz NOT NULL,
      consumed_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX otp_challenges_phone_created ON otp_challenges (phone_lookup, created_at DESC);

    -- Sandbox SMS inbox for isolated local development only (Q8). Never read by staging/production code.
    CREATE TABLE dev_sms_inbox (
      id bigserial PRIMARY KEY,
      phone_lookup text NOT NULL,
      body text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE admin_users (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      username text NOT NULL UNIQUE CHECK (username = lower(username) AND username ~ '^[a-z0-9._-]{3,40}$'),
      password_hash text NOT NULL,
      totp_secret_enc bytea,
      totp_enrolled_at timestamptz,
      state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','locked','disabled')),
      failed_logins int NOT NULL DEFAULT 0,
      last_login_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      created_by text NOT NULL
    );
    CREATE TABLE admin_used_totp_steps (
      admin_id uuid NOT NULL REFERENCES admin_users(id),
      time_step bigint NOT NULL,
      PRIMARY KEY (admin_id, time_step)
    );
    CREATE TABLE admin_mfa_challenges (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      admin_id uuid NOT NULL REFERENCES admin_users(id),
      token_hash text NOT NULL UNIQUE,
      attempts int NOT NULL DEFAULT 0,
      expires_at timestamptz NOT NULL,
      consumed_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE sessions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      subject_type text NOT NULL CHECK (subject_type IN ('user','admin')),
      subject_id uuid NOT NULL,
      device_label text CHECK (char_length(device_label) <= 60),
      created_at timestamptz NOT NULL DEFAULT now(),
      last_seen_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz NOT NULL,
      revoked_at timestamptz,
      revoke_reason text
    );
    CREATE INDEX sessions_subject ON sessions (subject_type, subject_id) WHERE revoked_at IS NULL;
    CREATE TABLE refresh_tokens (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      session_id uuid NOT NULL REFERENCES sessions(id),
      token_hash text NOT NULL UNIQUE,
      issued_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz NOT NULL,
      rotated_at timestamptz
    );
    CREATE TABLE step_up_grants (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      session_id uuid NOT NULL REFERENCES sessions(id),
      admin_id uuid NOT NULL REFERENCES admin_users(id),
      action text NOT NULL,
      expires_at timestamptz NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE permissions (code text PRIMARY KEY, description text NOT NULL);
    CREATE TABLE admin_roles (code text PRIMARY KEY);
    CREATE TABLE admin_role_permissions (
      role_code text NOT NULL REFERENCES admin_roles(code),
      permission_code text NOT NULL REFERENCES permissions(code),
      PRIMARY KEY (role_code, permission_code)
    );

    CREATE TABLE service_regions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      code text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_]{3,40}$'),
      name text NOT NULL,
      boundary geography(MultiPolygon, 4326) NOT NULL,
      status text NOT NULL DEFAULT 'disabled' CHECK (status IN ('disabled','enabled')),
      is_synthetic boolean NOT NULL,
      approval_status text NOT NULL DEFAULT 'UNAPPROVED' CHECK (approval_status IN ('UNAPPROVED','APPROVED')),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX service_regions_boundary ON service_regions USING gist (boundary);

    CREATE TABLE admin_role_assignments (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      admin_id uuid NOT NULL REFERENCES admin_users(id),
      role_code text NOT NULL REFERENCES admin_roles(code),
      region_id uuid REFERENCES service_regions(id),
      granted_by text NOT NULL,
      reason text NOT NULL CHECK (char_length(reason) >= 5),
      granted_at timestamptz NOT NULL DEFAULT now(),
      revoked_at timestamptz
    );
    CREATE UNIQUE INDEX admin_role_assignments_live ON admin_role_assignments
      (admin_id, role_code, coalesce(region_id, '00000000-0000-0000-0000-000000000000'::uuid))
      WHERE revoked_at IS NULL;

    -- Maker/checker for region/category activation and safeguarding policy changes (Q15).
    CREATE TABLE policy_change_requests (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      change_type text NOT NULL CHECK (change_type IN ('region.enable','region.disable')),
      target_id uuid NOT NULL,
      payload jsonb NOT NULL DEFAULT '{}',
      justification text NOT NULL CHECK (char_length(justification) >= 10),
      requested_by uuid NOT NULL REFERENCES admin_users(id),
      requested_at timestamptz NOT NULL DEFAULT now(),
      status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','withdrawn')),
      decided_by uuid REFERENCES admin_users(id),
      decided_at timestamptz,
      decision_reason text,
      CONSTRAINT no_self_approval CHECK (decided_by IS NULL OR decided_by <> requested_by)
    );
    CREATE UNIQUE INDEX one_pending_change_per_target ON policy_change_requests (change_type, target_id)
      WHERE status = 'pending';

    CREATE TABLE consent_notices (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      code text NOT NULL,
      version int NOT NULL CHECK (version >= 1),
      language text NOT NULL CHECK (language IN ('si','ta','en')),
      title text NOT NULL,
      body text NOT NULL,
      status text NOT NULL DEFAULT 'active' CHECK (status IN ('draft','active','retired')),
      approval_status text NOT NULL DEFAULT 'UNAPPROVED' CHECK (approval_status IN ('UNAPPROVED','APPROVED')),
      translation_status text NOT NULL DEFAULT 'source' CHECK (translation_status IN ('source','untranslated','reviewed')),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (code, version, language)
    );
    CREATE TABLE notice_acceptances (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid NOT NULL REFERENCES users(id),
      notice_id uuid NOT NULL REFERENCES consent_notices(id),
      processing_context text NOT NULL,
      accepted_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (user_id, notice_id)
    );
    """)

    # Tamper-evident audit: the database computes the hash chain and refuses UPDATE/DELETE/TRUNCATE.
    op.execute("""
    CREATE TABLE audit_logs (
      id bigserial PRIMARY KEY,
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      actor_type text NOT NULL CHECK (actor_type IN ('user','admin','system')),
      actor_id text NOT NULL,
      action text NOT NULL,
      entity_type text NOT NULL,
      entity_id text NOT NULL,
      details jsonb NOT NULL DEFAULT '{}',
      correlation_id text NOT NULL DEFAULT '-',
      prev_hash text NOT NULL,
      row_hash text NOT NULL
    );

    CREATE FUNCTION audit_row_digest(prev text, a audit_logs) RETURNS text
    LANGUAGE sql IMMUTABLE AS $$
      SELECT encode(sha256(convert_to(concat_ws('|', prev,
        to_char(a.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        a.actor_type, a.actor_id, a.action, a.entity_type, a.entity_id, a.details::text, a.correlation_id),
        'UTF8')), 'hex')
    $$;

    CREATE FUNCTION audit_chain_before_insert() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE last_hash text;
    BEGIN
      -- Serialize writers so every row links to exactly one predecessor.
      PERFORM pg_advisory_xact_lock(727274);
      SELECT row_hash INTO last_hash FROM audit_logs ORDER BY id DESC LIMIT 1;
      NEW.prev_hash := coalesce(last_hash, 'GENESIS');
      NEW.occurred_at := clock_timestamp();
      NEW.row_hash := audit_row_digest(NEW.prev_hash, NEW);
      RETURN NEW;
    END $$;

    CREATE FUNCTION audit_block_mutation() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'audit_logs is append-only' USING ERRCODE = 'insufficient_privilege';
    END $$;

    CREATE TRIGGER audit_chain BEFORE INSERT ON audit_logs FOR EACH ROW EXECUTE FUNCTION audit_chain_before_insert();
    CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON audit_logs FOR EACH ROW EXECUTE FUNCTION audit_block_mutation();
    CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_logs FOR EACH STATEMENT EXECUTE FUNCTION audit_block_mutation();

    -- Returns the first id whose stored hash does not match a recomputation, or NULL when intact.
    CREATE FUNCTION audit_verify_chain() RETURNS bigint
    LANGUAGE plpgsql STABLE AS $$
    DECLARE r audit_logs; expected_prev text := 'GENESIS';
    BEGIN
      FOR r IN SELECT * FROM audit_logs ORDER BY id LOOP
        IF r.prev_hash <> expected_prev OR r.row_hash <> audit_row_digest(r.prev_hash, r) THEN
          RETURN r.id;
        END IF;
        expected_prev := r.row_hash;
      END LOOP;
      RETURN NULL;
    END $$;
    """)

    values = ', '.join(f"('{c}', '{d}')" for c, d in PERMISSIONS)
    op.execute(f'INSERT INTO permissions(code, description) VALUES {values}')
    for role, perms in ROLES.items():
        op.execute(f"INSERT INTO admin_roles(code) VALUES ('{role}')")
        for p in perms:
            op.execute(f"INSERT INTO admin_role_permissions VALUES ('{role}', '{p}')")

    # Least privilege for the runtime role: reference tables read-only, audit insert-only.
    op.execute(f"""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE ON users, user_personas, otp_challenges, dev_sms_inbox, admin_users,
          admin_mfa_challenges, sessions, refresh_tokens, step_up_grants, service_regions,
          admin_role_assignments, policy_change_requests, consent_notices, notice_acceptances TO {APP_ROLE};
        GRANT SELECT, INSERT ON admin_used_totp_steps TO {APP_ROLE};
        GRANT SELECT ON permissions, admin_roles, admin_role_permissions TO {APP_ROLE};
        GRANT SELECT, INSERT ON audit_logs TO {APP_ROLE};
        GRANT EXECUTE ON FUNCTION audit_verify_chain() TO {APP_ROLE};
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};
      END IF;
    END $$;
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS audit_logs, notice_acceptances, consent_notices, policy_change_requests,
      admin_role_assignments, service_regions, admin_role_permissions, admin_roles, permissions,
      step_up_grants, refresh_tokens, sessions, admin_mfa_challenges, admin_used_totp_steps, admin_users,
      dev_sms_inbox, otp_challenges, user_personas, users CASCADE;
    DROP FUNCTION IF EXISTS audit_block_mutation(), audit_chain_before_insert();
    """)
