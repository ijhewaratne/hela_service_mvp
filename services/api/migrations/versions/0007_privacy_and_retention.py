"""Privacy-request intake and case tracking (Q66, FR-ID-08, §15.2) and a retention/legal-hold register (Q63, §15.3).

Retention periods are deliberately NULL/UNAPPROVED: nothing is deleted automatically until a reviewed lawful
schedule exists (DEC-09). The register supports dry-run reporting only.

Revision ID: 0007_privacy_and_retention
"""
from alembic import op

revision = '0007_privacy_and_retention'
down_revision = '0006_policy_publishing'
branch_labels = None
depends_on = None

# data category, what it covers (table), purpose, proposed lawful basis (to be confirmed by counsel)
CATEGORIES = [
    ('auth_logs', 'sessions', 'Account security and abuse prevention', 'legitimate interest (proposed)'),
    ('otp_challenges', 'otp_challenges', 'Sign-in verification', 'contract (proposed)'),
    ('identity_evidence', 'provider_evidence', 'Provider identity and qualification checks', 'legal obligation / contract (proposed)'),
    ('provider_approvals', 'provider_category_approvals', 'Evidence of who was approved and why', 'legal obligation (proposed)'),
    ('cancelled_requests', 'service_requests', 'Service requests that never became bookings', 'contract (proposed)'),
    ('completed_jobs', 'bookings', 'Completed bookings and their history', 'contract / legal obligation (proposed)'),
    ('incident_casefiles', 'incidents', 'Safety and dispute case handling', 'vital/legitimate interest (proposed)'),
    ('notifications', 'notifications', 'Delivery records of messages sent', 'contract (proposed)'),
    ('finance_records', 'settlements', 'Offline payment acknowledgements and receivables', 'legal obligation (proposed)'),
    ('audit_logs', 'audit_logs', 'Accountability for privileged actions', 'legal obligation (proposed)'),
]


def upgrade():
    op.execute("""
    CREATE TABLE privacy_requests (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      user_id uuid NOT NULL REFERENCES users(id),
      kind text NOT NULL CHECK (kind IN ('access_export','correction','deletion','objection','other')),
      details_enc bytea,
      status text NOT NULL DEFAULT 'received'
        CHECK (status IN ('received','identity_verified','in_progress','on_legal_hold','fulfilled','rejected')),
      due_at timestamptz NOT NULL,                  -- UNAPPROVED placeholder target; statutory timing pending (§15.1)
      handled_by uuid REFERENCES admin_users(id),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      closed_at timestamptz
    );
    CREATE TABLE privacy_request_events (
      id bigserial PRIMARY KEY,
      request_id uuid NOT NULL REFERENCES privacy_requests(id),
      actor_type text NOT NULL, actor_id text NOT NULL,
      from_status text, to_status text NOT NULL, note_enc bytea,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TRIGGER no_mutation BEFORE UPDATE OR DELETE ON privacy_request_events
      FOR EACH ROW EXECUTE FUNCTION block_history_mutation();

    CREATE TABLE retention_policies (
      data_category text PRIMARY KEY,
      source_table text NOT NULL,
      purpose text NOT NULL,
      lawful_basis text NOT NULL,
      retention_days int CHECK (retention_days > 0),   -- NULL = not decided (DEC-09)
      approval_status text NOT NULL DEFAULT 'UNAPPROVED' CHECK (approval_status IN ('UNAPPROVED','APPROVED')),
      deletion_method text NOT NULL DEFAULT 'manual review',
      reviewer text
    );

    CREATE TABLE legal_holds (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      entity_type text NOT NULL,
      entity_id text NOT NULL,
      reason text NOT NULL CHECK (char_length(reason) >= 10),
      placed_by uuid NOT NULL REFERENCES admin_users(id),
      placed_at timestamptz NOT NULL DEFAULT now(),
      released_by uuid REFERENCES admin_users(id),
      released_at timestamptz,
      CHECK (released_by IS NULL OR released_by <> placed_by)    -- releasing a hold needs a second person
    );
    CREATE UNIQUE INDEX one_live_hold ON legal_holds (entity_type, entity_id) WHERE released_at IS NULL;

    INSERT INTO permissions(code, description) VALUES ('privacy.handle', 'Handle privacy requests and legal holds');
    INSERT INTO admin_roles(code) VALUES ('privacy_officer');
    INSERT INTO admin_role_permissions VALUES ('privacy_officer', 'privacy.handle'), ('super_admin', 'privacy.handle');
    """)
    values = ', '.join(f"('{c}', '{t}', '{p}', '{b}')" for c, t, p, b in CATEGORIES)
    op.execute(f'INSERT INTO retention_policies(data_category, source_table, purpose, lawful_basis) VALUES {values}')
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hela_app') THEN
        GRANT SELECT, INSERT, UPDATE ON privacy_requests, legal_holds TO hela_app;
        GRANT SELECT, INSERT ON privacy_request_events TO hela_app;
        GRANT SELECT ON retention_policies TO hela_app;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO hela_app;
      END IF;
    END $$;
    """)


def downgrade():
    op.execute("""
    DROP TABLE IF EXISTS legal_holds, retention_policies, privacy_request_events, privacy_requests CASCADE;
    DELETE FROM admin_role_permissions WHERE permission_code = 'privacy.handle';
    DELETE FROM admin_role_assignments WHERE role_code = 'privacy_officer';   -- the role ceases to exist
    DELETE FROM admin_roles WHERE code = 'privacy_officer';
    DELETE FROM permissions WHERE code = 'privacy.handle';
    """)
