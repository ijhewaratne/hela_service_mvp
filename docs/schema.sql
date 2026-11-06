-- Hela Service independent marketplace starter schema (PostgreSQL 15+ / PostGIS)
-- Review before production: permissions/RLS, partitioning, retention, masking,
-- exclusion constraints and transition triggers are not fully implemented here.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  phone_e164 text UNIQUE NOT NULL,
  display_name text,
  preferred_language text NOT NULL DEFAULT 'si' CHECK (preferred_language IN ('si','ta','en')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','closed')),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_roles (
  user_id uuid NOT NULL REFERENCES users(id),
  role text NOT NULL CHECK (role IN ('customer','provider','admin','super_admin')),
  PRIMARY KEY (user_id,role)
);

CREATE TABLE service_categories (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text NOT NULL UNIQUE,
  name_en text NOT NULL,
  name_si text,
  name_ta text,
  enabled boolean NOT NULL DEFAULT false,
  price_method text NOT NULL CHECK (price_method IN ('fixed','hourly','quote_required')),
  requires_extra_safeguards boolean NOT NULL DEFAULT false,
  policy_version text NOT NULL DEFAULT 'v1'
);

CREATE TABLE customer_addresses (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id uuid NOT NULL REFERENCES users(id),
  label text NOT NULL,
  address_text text NOT NULL,
  point geography(Point,4326) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_address_geo ON customer_addresses USING gist(point);

CREATE TABLE provider_profiles (
  user_id uuid PRIMARY KEY REFERENCES users(id),
  profile_status text NOT NULL DEFAULT 'applicant'
    CHECK (profile_status IN ('applicant','under_review','active','suspended')),
  service_radius_km numeric(7,2) NOT NULL DEFAULT 5 CHECK (service_radius_km > 0),
  base_point geography(Point,4326),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_provider_geo ON provider_profiles USING gist(base_point);

CREATE TABLE provider_categories (
  provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
  category_id uuid NOT NULL REFERENCES service_categories(id),
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','needs_more_info','approved','denied','revoked')),
  reviewed_by uuid REFERENCES users(id),
  reviewed_at timestamptz,
  expires_at timestamptz,
  verification_notes text,
  PRIMARY KEY (provider_id,category_id)
);

CREATE TABLE provider_documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
  category_id uuid REFERENCES service_categories(id),
  evidence_type text NOT NULL,
  object_storage_key text NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected','expired')),
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE provider_availability (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  CHECK (ends_at > starts_at)
);
CREATE INDEX idx_availability ON provider_availability(provider_id, starts_at, ends_at);

CREATE TABLE service_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  booker_id uuid NOT NULL REFERENCES users(id),
  address_id uuid NOT NULL REFERENCES customer_addresses(id),
  category_id uuid NOT NULL REFERENCES service_categories(id),
  beneficiary_authorized boolean NOT NULL DEFAULT false,
  task_details text NOT NULL,
  start_at timestamptz NOT NULL,
  end_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'requested' CHECK (status IN ('requested','offering','accepted','cancelled','unfulfilled')),
  pricing_policy_version text NOT NULL,
  estimate_minor bigint CHECK (estimate_minor >= 0),
  currency text NOT NULL DEFAULT 'LKR' CHECK (currency='LKR'),
  idempotency_key text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (end_at > start_at),
  UNIQUE (booker_id,idempotency_key)
);

CREATE TABLE offers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id uuid NOT NULL REFERENCES service_requests(id),
  provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','declined','expired','revoked')),
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_one_accepted_offer_per_request ON offers(request_id) WHERE status='accepted';

CREATE TABLE bookings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id uuid NOT NULL UNIQUE REFERENCES service_requests(id),
  provider_id uuid NOT NULL REFERENCES provider_profiles(user_id),
  accepted_offer_id uuid NOT NULL UNIQUE REFERENCES offers(id),
  status text NOT NULL DEFAULT 'accepted' CHECK (status IN
    ('accepted','en_route','arrived','in_progress','completed_pending_confirmation',
    'completed','disputed','incident_hold','cancelled','cancelled_after_review')),
  version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE booking_events (
  id bigserial PRIMARY KEY,
  booking_id uuid NOT NULL REFERENCES bookings(id),
  actor_id uuid REFERENCES users(id),
  from_status text,
  to_status text NOT NULL,
  reason_code text,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_booking_events ON booking_events(booking_id, occurred_at);

CREATE TABLE incidents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id uuid REFERENCES bookings(id),
  reporter_id uuid NOT NULL REFERENCES users(id),
  kind text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('low','medium','high','critical')),
  description text NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','investigating','resolved','closed')),
  assigned_admin_id uuid REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id uuid NOT NULL UNIQUE REFERENCES bookings(id),
  customer_id uuid NOT NULL REFERENCES users(id),
  rating integer NOT NULL CHECK (rating BETWEEN 1 AND 5),
  comment text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE settlement_records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id uuid NOT NULL UNIQUE REFERENCES bookings(id),
  agreed_amount_minor bigint NOT NULL CHECK (agreed_amount_minor >= 0),
  commission_due_minor bigint NOT NULL DEFAULT 0 CHECK (commission_due_minor >= 0),
  currency text NOT NULL DEFAULT 'LKR' CHECK (currency='LKR'),
  payment_status text NOT NULL DEFAULT 'UNPAID'
    CHECK (payment_status IN ('UNPAID','CUSTOMER_REPORTED_PAID','PROVIDER_CONFIRMED','DISPUTED')),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_logs (
  id bigserial PRIMARY KEY,
  actor_id uuid REFERENCES users(id),
  action text NOT NULL,
  object_type text NOT NULL,
  object_id text NOT NULL,
  reason_code text,
  before_state jsonb,
  after_state jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Enforce server-side in a SERIALIZABLE/row-locked acceptance transaction:
-- (a) recheck offer expiry, approved category, user status, credential expiry,
--     availability, geographic coverage, conflicts and feature flag;
-- (b) ensure at most one offer accepted per request;
-- (c) create booking and emit event/outbox atomically.
-- A production schema needs stronger DB-level protections for provider time overlap,
-- access policies, beneficiary consent and immutable audit events.
