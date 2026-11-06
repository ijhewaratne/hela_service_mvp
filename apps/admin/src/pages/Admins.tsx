import { useState } from 'react';
import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Badge, ErrorBanner, Notice, ReasonAction, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;
const ROLES = ['operations_admin', 'verification_reviewer', 'incident_responder', 'finance_reconciler', 'auditor', 'super_admin'];

/** Role grants (Q11/Q12): step-up, a reason, never to yourself. New admins are created by the operator CLI with MFA. */
export function Admins() {
  const { me } = useAuth();
  const admins = useLoad(() => api.request<Row[]>('/v1/admin/admins'));
  const [role, setRole] = useState('auditor');
  return (
    <>
      <h1>Administrators</h1>
      <Notice>New administrator accounts are created with <code>python -m hela.cli create-admin</code>, which enrols MFA
        out of band. This page only grants and revokes roles.</Notice>
      <ErrorBanner error={admins.error} />
      <Section title="Accounts and roles">
        <Table rows={admins.data} columns={[
          { key: 'username', label: 'Username' }, { key: 'state', label: 'State', render: (r) => <Badge value={String(r.state)} /> },
          { key: 'last_login_at', label: 'Last sign-in' },
          { key: 'roles', label: 'Roles', render: (r) => (r.roles as Row[]).map((g) => (
            <div key={String(g.assignment_id)} className="row"><Badge value={String(g.role)} />
              {g.region_id ? <span className="muted small">region {String(g.region_id).slice(0, 8)}</span> : <span className="muted small">all regions</span>}
              {r.id !== me?.id && <ReasonAction label="Revoke" danger onSubmit={async (reason) => {
                await api.request(`/v1/admin/role-assignments/${g.assignment_id}/revoke`, { body: { reason } });
                await admins.reload();
              }} />}
            </div>)) },
          { key: 'grant', label: '', render: (r) => r.id === me?.id ? <span className="muted">You cannot change your own roles.</span> : (
            <ReasonAction label="Grant role" onSubmit={async (reason) => {
              await api.request(`/v1/admin/admins/${r.id}/roles`, { body: { role_code: role, reason } });
              await admins.reload();
            }} extra={(<><label htmlFor={`role-${r.id}`}>Role</label>
              <select id={`role-${r.id}`} value={role} onChange={(e) => setRole(e.target.value)}>{ROLES.map((x) => <option key={x}>{x}</option>)}</select></>)} />) },
        ]} />
      </Section>
    </>
  );
}
