"""M02 provider trust: categories and versioned policies, provider applications, private evidence,
per-category approvals with history, sensitive-access logging.

Revision ID: 0002_provider_trust
"""
from alembic import op

revision = '0002_provider_trust'
down_revision = '0001_foundation'
branch_labels = None
depends_on = None

APP_ROLE = 'hela_app'


def upgrade():
    op.execute("""
    CREATE TABLE service_categories (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      code text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_]{3,40}$'),
      display_name text NOT NULL,
      -- spec §4.1 classification; DISABLED and OUT_OF_SCOPE can never be enabled through any interface (INV-09)
      risk_class text NOT NULL CHECK (risk_class IN ('pilot_candidate','gated','disabled','out_of_scope')),
      status text NOT NULL DEFAULT 'disabled' CHECK (status IN ('enabled','disabled')),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT never_enable_forbidden CHECK (status = 'disabled' OR risk_class IN ('pilot_candidate','gated'))
    );

    -- Versioned category terms (spec §4.2). Content is immutable; changes create a new version.
    CREATE TABLE category_policies (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      category_code text NOT NULL REFERENCES service_categories(code),
      version int NOT NULL CHECK (version >= 1),
      status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','retired')),
      approval_status text NOT NULL DEFAULT 'UNAPPROVED' CHECK (approval_status IN ('UNAPPROVED','APPROVED')),
      allowed_tasks jsonb NOT NULL DEFAULT '[]',
      prohibited_tasks jsonb NOT NULL DEFAULT '[]',
      min_duration_minutes int NOT NULL CHECK (min_duration_minutes > 0),
      max_duration_minutes int NOT NULL CHECK (max_duration_minutes >= min_duration_minutes),
      max_radius_km numeric(5,2) NOT NULL CHECK (max_radius_km > 0),
      -- [{"type": "national_id", "count": 1, "review_interval_days": 365}, ...]
      required_evidence jsonb NOT NULL DEFAULT '[]',
      safeguards_required boolean NOT NULL DEFAULT false,
      approval_validity_days int NOT NULL DEFAULT 365 CHECK (approval_validity_days > 0),
      min_provider_age int NOT NULL DEFAULT 18 CHECK (min_provider_age >= 18),
      on_demand_allowed boolean NOT NULL DEFAULT false,
      terms jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (category_code, version)
    );
    CREATE UNIQUE INDEX one_active_policy_per_category ON category_policies (category_code) WHERE status = 'active';

    CREATE FUNCTION category_policy_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (to_jsonb(NEW) - 'status' - 'approval_status') <> (to_jsonb(OLD) - 'status' - 'approval_status') THEN
        RAISE EXCEPTION 'category policy content is immutable; create a new version'
          USING ERRCODE = 'insufficient_privilege';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER category_policy_immutable BEFORE UPDATE ON category_policies
      FOR EACH ROW EXECUTE FUNCTION category_policy_immutable();

    CREATE TABLE provider_profiles (
      user_id uuid PRIMARY KEY REFERENCES users(id),
      application_state text NOT NULL DEFAULT 'draft'
        CHECK (application_state IN ('draft','submitted','needs_more_info','under_review','approved','denied')),
      account_state text NOT NULL DEFAULT 'active' CHECK (account_state IN ('active','suspended','deactivated')),
      legal_name_enc bytea NOT NULL,
      date_of_birth_enc bytea NOT NULL,
      identity_verified_at timestamptz,
      identity_verified_by uuid REFERENCES admin_users(id),
      age_verified_at timestamptz,
      age_verified_by uuid REFERENCES admin_users(id),
      age_verification_method text CHECK (age_verification_method IN ('id_document_inspected','in_person_id_check')),
      submitted_at timestamptz,
      suspended_reason text,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT approved_requires_checks CHECK (application_state <> 'approved'
        OR (identity_verified_at IS NOT NULL AND age_verified_at IS NOT NULL))
    );

    CREATE TABLE provider_application_events (
      id bigserial PRIMARY KEY,
      provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
      actor_type text NOT NULL CHECK (actor_type IN ('user','admin','system')),
      actor_id text NOT NULL,
      from_state text,
      to_state text NOT NULL,
      reason text,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE provider_evidence (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
      evidence_type text NOT NULL CHECK (evidence_type IN ('national_id','address_proof','reference',
                                                           'training_record','police_clearance','other')),
      category_code text REFERENCES service_categories(code),
      object_key text,                         -- NULL when the upload was quarantined and not stored
      wrapped_data_key bytea,
      sha256 text NOT NULL,
      media_type text NOT NULL CHECK (media_type IN ('application/pdf','image/jpeg','image/png')),
      size_bytes int NOT NULL CHECK (size_bytes > 0),
      scan_state text NOT NULL CHECK (scan_state IN ('pending','clean','infected','rejected')),
      scan_detail text,
      review_state text NOT NULL DEFAULT 'pending'
        CHECK (review_state IN ('pending','accepted','rejected','needs_more_info','superseded','expired')),
      document_expires_on date,                -- printed on the document, if it has one (Q35)
      next_review_due date,                    -- periodic re-review, separate from document expiry (Q35)
      reviewed_by uuid REFERENCES admin_users(id),
      reviewed_at timestamptz,
      review_reason text,
      uploaded_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT stored_iff_not_quarantined CHECK ((scan_state IN ('infected','rejected')) = (object_key IS NULL)),
      CONSTRAINT accept_only_clean CHECK (review_state <> 'accepted' OR scan_state = 'clean')
    );
    CREATE INDEX provider_evidence_provider ON provider_evidence (provider_id, evidence_type);

    CREATE TABLE provider_category_approvals (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
      category_code text NOT NULL REFERENCES service_categories(code),
      policy_id uuid NOT NULL REFERENCES category_policies(id),
      status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','under_review','needs_more_info',
                                                              'approved','denied','revoked','expired')),
      valid_from timestamptz,
      valid_until timestamptz,
      probation_until date,
      probation_min_jobs int CHECK (probation_min_jobs >= 0),
      decided_by uuid REFERENCES admin_users(id),
      decided_at timestamptz,
      decision_reason text,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (provider_id, category_code),
      CONSTRAINT approved_has_validity CHECK (status <> 'approved'
        OR (valid_from IS NOT NULL AND valid_until > valid_from AND decided_by IS NOT NULL))
    );

    -- Full decision history; the approval row holds only the latest state (FR-VER-03).
    CREATE TABLE provider_approval_events (
      id bigserial PRIMARY KEY,
      approval_id uuid NOT NULL REFERENCES provider_category_approvals(id),
      actor_type text NOT NULL CHECK (actor_type IN ('user','admin','system')),
      actor_id text NOT NULL,
      from_status text,
      to_status text NOT NULL,
      policy_id uuid NOT NULL REFERENCES category_policies(id),
      reason text,
      evidence_ids uuid[] NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE sensitive_access_logs (
      id bigserial PRIMARY KEY,
      admin_id uuid NOT NULL REFERENCES admin_users(id),
      session_id uuid NOT NULL REFERENCES sessions(id),
      resource_type text NOT NULL,
      resource_id text NOT NULL,
      access_kind text NOT NULL,
      purpose text NOT NULL CHECK (char_length(purpose) >= 5),
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE evidence_access_tokens (
      token_hash text PRIMARY KEY,
      evidence_id uuid NOT NULL REFERENCES provider_evidence(id),
      admin_id uuid NOT NULL REFERENCES admin_users(id),
      expires_at timestamptz NOT NULL,
      used_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE FUNCTION block_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
    END $$;
    CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON provider_application_events
      FOR EACH ROW EXECUTE FUNCTION block_history_mutation();
    CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON provider_approval_events
      FOR EACH ROW EXECUTE FUNCTION block_history_mutation();
    CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON sensitive_access_logs
      FOR EACH ROW EXECUTE FUNCTION block_history_mutation();

    ALTER TABLE policy_change_requests DROP CONSTRAINT policy_change_requests_change_type_check;
    ALTER TABLE policy_change_requests ADD CONSTRAINT policy_change_requests_change_type_check
      CHECK (change_type IN ('region.enable','region.disable','category.enable','category.disable'));
    """)

    op.execute(f"""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, UPDATE ON service_categories TO {APP_ROLE};
        GRANT SELECT ON category_policies TO {APP_ROLE};
        GRANT SELECT, INSERT, UPDATE ON provider_profiles, provider_evidence, provider_category_approvals,
          evidence_access_tokens TO {APP_ROLE};
        GRANT SELECT, INSERT ON provider_application_events, provider_approval_events, sensitive_access_logs
          TO {APP_ROLE};
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};
      END IF;
    END $$;
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS evidence_access_tokens, sensitive_access_logs, provider_approval_events,
      provider_category_approvals, provider_evidence, provider_application_events, provider_profiles,
      category_policies, service_categories CASCADE;
    DROP FUNCTION IF EXISTS block_history_mutation(), category_policy_immutable();
    ALTER TABLE policy_change_requests DROP CONSTRAINT policy_change_requests_change_type_check;
    ALTER TABLE policy_change_requests ADD CONSTRAINT policy_change_requests_change_type_check
      CHECK (change_type IN ('region.enable','region.disable'));
    """)
