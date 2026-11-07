"""Policy and price versions drafted and published through the API with maker/checker (AT-027).

The runtime role may INSERT new versions (content is immutable thereafter, trigger from 0002) and may change only
the `status` column. `approval_status` stays owner-only: APPROVED records an external business/legal sign-off.

Revision ID: 0006_policy_publishing
"""
from alembic import op

revision = '0006_policy_publishing'
down_revision = '0005_audit_sealing'
branch_labels = None
depends_on = None

TYPES = ("'region.enable','region.disable','category.enable','category.disable',"
         "'policy.publish','price.publish'")


def upgrade():
    op.execute(f"""
    ALTER TABLE policy_change_requests DROP CONSTRAINT policy_change_requests_change_type_check;
    ALTER TABLE policy_change_requests ADD CONSTRAINT policy_change_requests_change_type_check
      CHECK (change_type IN ({TYPES}));
    ALTER TABLE category_policies ADD COLUMN drafted_by uuid REFERENCES admin_users(id);
    ALTER TABLE price_rules ADD COLUMN drafted_by uuid REFERENCES admin_users(id);
    """)
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hela_app') THEN
        GRANT INSERT ON category_policies, price_rules TO hela_app;
        GRANT UPDATE (status) ON category_policies, price_rules TO hela_app;
      END IF;
    END $$;
    """)


def downgrade():
    op.execute("""
    REVOKE INSERT, UPDATE ON category_policies, price_rules FROM hela_app;
    ALTER TABLE category_policies DROP COLUMN drafted_by;
    ALTER TABLE price_rules DROP COLUMN drafted_by;
    ALTER TABLE policy_change_requests DROP CONSTRAINT policy_change_requests_change_type_check;
    ALTER TABLE policy_change_requests ADD CONSTRAINT policy_change_requests_change_type_check
      CHECK (change_type IN ('region.enable','region.disable','category.enable','category.disable'));
    """)
