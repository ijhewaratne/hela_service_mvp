"""M03 booking engine: addresses, beneficiaries, provider availability, pricing and quotes, requests, matching,
offers, bookings with DB-enforced single assignment and non-overlap, settlement, incidents, reviews,
notification outbox and durable jobs.

Revision ID: 0003_booking_engine
"""
from alembic import op

revision = '0003_booking_engine'
down_revision = '0002_provider_trust'
branch_labels = None
depends_on = None

APP_ROLE = 'hela_app'
HISTORY = ['request_events', 'booking_events', 'settlement_events', 'incident_actions']


def upgrade():
    op.execute("""
    -- Exact address and pin are encrypted; match_point is rounded (~500 m) and only used for coarse matching (Q17, Q65).
    CREATE TABLE customer_addresses (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      owner_id uuid NOT NULL REFERENCES users(id),
      label text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 40),
      address_enc bytea NOT NULL,
      pin_enc bytea NOT NULL,
      match_point geography(Point, 4326) NOT NULL,
      region_id uuid NOT NULL REFERENCES service_regions(id),
      area_label text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      archived_at timestamptz
    );
    CREATE INDEX customer_addresses_owner ON customer_addresses (owner_id) WHERE archived_at IS NULL;

    CREATE TABLE beneficiaries (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      owner_id uuid NOT NULL REFERENCES users(id),
      name_enc bytea NOT NULL,
      phone_enc bytea NOT NULL,
      phone_last3 text NOT NULL CHECK (phone_last3 ~ '^[0-9]{3}$'),
      relationship text NOT NULL CHECK (char_length(relationship) BETWEEN 2 AND 40),
      created_at timestamptz NOT NULL DEFAULT now(),
      archived_at timestamptz
    );

    CREATE TABLE provider_service_areas (
      provider_id uuid PRIMARY KEY REFERENCES provider_profiles(user_id),
      base_point geography(Point, 4326) NOT NULL,   -- rounded; the provider's home is never stored exactly
      radius_km numeric(5,2) NOT NULL CHECK (radius_km > 0 AND radius_km <= 25),
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX provider_service_areas_point ON provider_service_areas USING gist (base_point);

    -- Weekly hours are local Asia/Colombo wall-clock times (Q43).
    CREATE TABLE provider_weekly_availability (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
      weekday int NOT NULL CHECK (weekday BETWEEN 0 AND 6),     -- 0 = Monday
      start_local time NOT NULL,
      end_local time NOT NULL,
      CHECK (end_local > start_local)
    );
    CREATE TABLE provider_blackouts (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
      period tstzrange NOT NULL CHECK (NOT isempty(period)),
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX provider_blackouts_period ON provider_blackouts USING gist (provider_id, period);

    -- Versioned synthetic price rules in integer minor units (Q29, Q30). Content immutable like policies.
    CREATE TABLE price_rules (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      category_code text NOT NULL REFERENCES service_categories(code),
      version int NOT NULL CHECK (version >= 1),
      status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','retired')),
      approval_status text NOT NULL DEFAULT 'UNAPPROVED' CHECK (approval_status IN ('UNAPPROVED','APPROVED')),
      currency text NOT NULL DEFAULT 'LKR' CHECK (currency = 'LKR'),
      basis text NOT NULL DEFAULT 'hourly_estimate' CHECK (basis IN ('fixed','hourly_estimate','quote_required')),
      hourly_rate_minor bigint NOT NULL CHECK (hourly_rate_minor >= 0),
      base_fee_minor bigint NOT NULL DEFAULT 0 CHECK (base_fee_minor >= 0),
      travel_fee_minor bigint NOT NULL DEFAULT 0 CHECK (travel_fee_minor >= 0),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (category_code, version)
    );
    CREATE UNIQUE INDEX one_active_price_rule ON price_rules (category_code) WHERE status = 'active';
    CREATE TRIGGER price_rule_immutable BEFORE UPDATE ON price_rules
      FOR EACH ROW EXECUTE FUNCTION category_policy_immutable();

    CREATE TABLE quotes (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      customer_id uuid NOT NULL REFERENCES users(id),
      address_id uuid NOT NULL REFERENCES customer_addresses(id),
      region_id uuid NOT NULL REFERENCES service_regions(id),
      category_code text NOT NULL REFERENCES service_categories(code),
      policy_id uuid NOT NULL REFERENCES category_policies(id),
      price_rule_id uuid NOT NULL REFERENCES price_rules(id),
      start_at timestamptz NOT NULL,
      duration_minutes int NOT NULL CHECK (duration_minutes > 0),
      basis text NOT NULL,
      currency text NOT NULL CHECK (currency = 'LKR'),
      components jsonb NOT NULL,
      total_minor bigint NOT NULL CHECK (total_minor >= 0),
      expires_at timestamptz NOT NULL,
      accepted_at timestamptz,
      used_by_request uuid UNIQUE,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE service_requests (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      customer_id uuid NOT NULL REFERENCES users(id),
      address_id uuid NOT NULL REFERENCES customer_addresses(id),
      region_id uuid NOT NULL REFERENCES service_regions(id),
      category_code text NOT NULL REFERENCES service_categories(code),
      policy_id uuid NOT NULL REFERENCES category_policies(id),
      quote_id uuid NOT NULL UNIQUE REFERENCES quotes(id),
      beneficiary_id uuid REFERENCES beneficiaries(id),
      task_codes jsonb NOT NULL,
      task_note_enc bytea,
      start_at timestamptz NOT NULL,
      end_at timestamptz NOT NULL CHECK (end_at > start_at),
      status text NOT NULL CHECK (status IN ('requested','awaiting_beneficiary','offering','accepted',
                                             'unfulfilled','cancelled','expired')),
      dispatch_round int NOT NULL DEFAULT 0,
      idempotency_key text NOT NULL,
      payload_hash text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (customer_id, idempotency_key)
    );
    CREATE TABLE request_events (
      id bigserial PRIMARY KEY,
      request_id uuid NOT NULL REFERENCES service_requests(id),
      actor_type text NOT NULL, actor_id text NOT NULL,
      from_status text, to_status text NOT NULL, reason text,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE beneficiary_consents (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      request_id uuid NOT NULL UNIQUE REFERENCES service_requests(id),
      beneficiary_id uuid NOT NULL REFERENCES beneficiaries(id),
      code_hash text NOT NULL,
      attempts int NOT NULL DEFAULT 0,
      status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','confirmed','declined','expired','staff_confirmed')),
      expires_at timestamptz NOT NULL,
      decided_at timestamptz,
      staff_admin_id uuid REFERENCES admin_users(id),
      staff_reason text,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE matching_runs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      request_id uuid NOT NULL REFERENCES service_requests(id),
      round int NOT NULL,
      config_version text NOT NULL,
      candidate_count int NOT NULL,
      eligible_count int NOT NULL,
      exclusions jsonb NOT NULL,
      ranked jsonb NOT NULL,
      outcome text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE offers (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      request_id uuid NOT NULL REFERENCES service_requests(id),
      provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
      matching_run_id uuid NOT NULL REFERENCES matching_runs(id),
      status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','declined','expired','revoked')),
      expires_at timestamptz NOT NULL,
      distance_band text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      responded_at timestamptz,
      UNIQUE (request_id, provider_id)
    );
    CREATE INDEX offers_provider_pending ON offers (provider_id) WHERE status = 'pending';

    CREATE TABLE bookings (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      request_id uuid NOT NULL UNIQUE REFERENCES service_requests(id),
      status text NOT NULL CHECK (status IN ('accepted','en_route','arrived','in_progress',
        'completed_pending_confirmation','completed','disputed','incident_hold','cancelled',
        'cancelled_after_review','reassigning')),
      version int NOT NULL DEFAULT 1,
      pin_enc bytea NOT NULL,
      pin_attempts int NOT NULL DEFAULT 0,
      completed_pending_since timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    );

    -- INV-01 / INV-02: one live assignment per booking; a provider never holds overlapping live assignments.
    CREATE TABLE booking_assignments (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      booking_id uuid NOT NULL REFERENCES bookings(id),
      provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
      offer_id uuid NOT NULL UNIQUE REFERENCES offers(id),
      busy tstzrange NOT NULL,                 -- [start, end + travel buffer)
      active boolean NOT NULL DEFAULT true,
      started_at timestamptz NOT NULL DEFAULT now(),
      ended_at timestamptz,
      end_reason text,
      CONSTRAINT no_overlapping_assignments EXCLUDE USING gist (provider_id WITH =, busy WITH &&) WHERE (active),
      CHECK (active OR ended_at IS NOT NULL)
    );
    CREATE UNIQUE INDEX one_live_assignment_per_booking ON booking_assignments (booking_id) WHERE active;

    CREATE TABLE booking_events (
      id bigserial PRIMARY KEY,
      booking_id uuid NOT NULL REFERENCES bookings(id),
      actor_type text NOT NULL, actor_id text NOT NULL,
      from_status text, to_status text NOT NULL, version int NOT NULL, reason text,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE idempotency_records (
      actor_type text NOT NULL, actor_id text NOT NULL, scope text NOT NULL, key text NOT NULL,
      request_hash text NOT NULL, status_code int NOT NULL, response jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (actor_type, actor_id, scope, key)
    );

    -- Offline settlement. INV-10: no row can ever claim Hela holds the money.
    CREATE TABLE settlements (
      booking_id uuid PRIMARY KEY REFERENCES bookings(id),
      currency text NOT NULL CHECK (currency = 'LKR'),
      agreed_amount_minor bigint NOT NULL CHECK (agreed_amount_minor >= 0),
      price_components jsonb NOT NULL,
      status text NOT NULL DEFAULT 'UNPAID'
        CHECK (status IN ('UNPAID','CUSTOMER_REPORTED_PAID','PROVIDER_CONFIRMED','DISPUTED','RESOLVED')),
      resolution text CHECK (resolution IN ('paid','unpaid')),
      customer_reference text,
      platform_collected_funds boolean NOT NULL DEFAULT false CHECK (platform_collected_funds = false),
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE settlement_events (
      id bigserial PRIMARY KEY,
      booking_id uuid NOT NULL REFERENCES settlements(booking_id),
      actor_type text NOT NULL, actor_id text NOT NULL,
      from_status text, to_status text NOT NULL, note_enc bytea,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE commission_receivables (
      booking_id uuid PRIMARY KEY REFERENCES bookings(id),
      rate_bps int NOT NULL CHECK (rate_bps >= 0),
      amount_minor bigint NOT NULL CHECK (amount_minor >= 0),
      status text NOT NULL DEFAULT 'not_invoiced' CHECK (status IN ('not_invoiced','invoiced','settled','waived')),
      note text NOT NULL DEFAULT 'Placeholder 0% rate; commission terms pending DEC-06',
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE incidents (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      booking_id uuid REFERENCES bookings(id),
      reporter_type text NOT NULL CHECK (reporter_type IN ('user','admin')),
      reporter_id uuid NOT NULL,
      kind text NOT NULL CHECK (kind IN ('safety','property_damage','harassment','service_mismatch','emergency',
                                         'payment_dispute','no_entry','other')),
      severity text NOT NULL CHECK (severity IN ('low','medium','high','critical')),
      description_enc bytea NOT NULL,
      status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','acknowledged','investigating','resolved','closed')),
      urgent boolean NOT NULL,
      assigned_to uuid REFERENCES admin_users(id),
      first_response_due timestamptz NOT NULL,
      first_response_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE incident_actions (
      id bigserial PRIMARY KEY,
      incident_id uuid NOT NULL REFERENCES incidents(id),
      admin_id uuid REFERENCES admin_users(id),
      action text NOT NULL,
      note_enc bytea,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE reviews (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      booking_id uuid NOT NULL UNIQUE REFERENCES bookings(id),
      customer_id uuid NOT NULL REFERENCES users(id),
      provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
      rating int NOT NULL CHECK (rating BETWEEN 1 AND 5),
      text_enc bytea,
      moderation_state text NOT NULL CHECK (moderation_state IN ('no_text','pending','approved','rejected')),
      pii_flag boolean NOT NULL DEFAULT false,
      moderated_by uuid REFERENCES admin_users(id),
      moderated_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE ops_alerts (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      kind text NOT NULL,
      ref_type text NOT NULL,
      ref_id uuid NOT NULL,
      severity text NOT NULL CHECK (severity IN ('info','warning','urgent')),
      status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
      created_at timestamptz NOT NULL DEFAULT now(),
      closed_at timestamptz,
      UNIQUE (kind, ref_id)
    );

    -- Notification outbox (Q60): minimal-PII template keys; delivery via adapters with attempt tracking.
    CREATE TABLE notifications (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      recipient_type text NOT NULL CHECK (recipient_type IN ('user','beneficiary')),
      recipient_id uuid NOT NULL,
      channel text NOT NULL CHECK (channel IN ('sms')),
      template_key text NOT NULL,
      params jsonb NOT NULL DEFAULT '{}',
      status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','dead')),
      attempts int NOT NULL DEFAULT 0,
      last_error text,
      dedupe_key text NOT NULL UNIQUE,
      created_at timestamptz NOT NULL DEFAULT now(),
      sent_at timestamptz
    );

    -- Durable jobs (Q69): leased with FOR UPDATE SKIP LOCKED, retried with backoff, dead-lettered.
    CREATE TABLE jobs (
      id bigserial PRIMARY KEY,
      kind text NOT NULL,
      payload jsonb NOT NULL,
      run_at timestamptz NOT NULL DEFAULT now(),
      status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','dead')),
      attempts int NOT NULL DEFAULT 0,
      max_attempts int NOT NULL DEFAULT 5,
      locked_until timestamptz,
      locked_by text,
      last_error text,
      dedupe_key text UNIQUE,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX jobs_due ON jobs (run_at) WHERE status IN ('queued','running');

    INSERT INTO permissions(code, description) VALUES ('review.moderate', 'Moderate customer review text');
    INSERT INTO admin_role_permissions VALUES ('super_admin', 'review.moderate'), ('operations_admin', 'review.moderate');
    INSERT INTO admin_role_permissions VALUES ('operations_admin', 'incident.respond');
    """)
    for table in HISTORY:
        op.execute(f'CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON {table} '
                   'FOR EACH ROW EXECUTE FUNCTION block_history_mutation()')

    op.execute(f"""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE ON customer_addresses, beneficiaries, provider_service_areas, quotes,
          service_requests, beneficiary_consents, offers, bookings, booking_assignments, settlements,
          commission_receivables, incidents, reviews, ops_alerts, notifications, jobs TO {APP_ROLE};
        GRANT SELECT, INSERT, DELETE ON provider_weekly_availability, provider_blackouts TO {APP_ROLE};
        GRANT SELECT, INSERT ON request_events, booking_events, settlement_events, incident_actions,
          matching_runs, idempotency_records TO {APP_ROLE};
        GRANT SELECT ON price_rules TO {APP_ROLE};
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};
      END IF;
    END $$;
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS jobs, notifications, ops_alerts, reviews, incident_actions, incidents,
      commission_receivables, settlement_events, settlements, idempotency_records, booking_events,
      booking_assignments, bookings, offers, matching_runs, beneficiary_consents, request_events,
      service_requests, quotes, price_rules, provider_blackouts, provider_weekly_availability,
      provider_service_areas, beneficiaries, customer_addresses CASCADE;
    DELETE FROM admin_role_permissions WHERE permission_code = 'review.moderate'
      OR (role_code = 'operations_admin' AND permission_code = 'incident.respond');
    DELETE FROM permissions WHERE code = 'review.moderate';
    """)
