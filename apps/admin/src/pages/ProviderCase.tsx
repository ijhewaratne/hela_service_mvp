import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, ErrorBanner, fmt, Notice, ReasonAction, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;
interface Case {
  provider_id: string;
  application_state: string;
  account_state: string;
  identity_verified_at: string | null;
  age_verified_at: string | null;
  age_verification_method: string | null;
  suspended_reason: string | null;
  evidence: Row[];
  approvals: (Row & { eligibility_gaps: string[] })[];
  application_history: Row[];
}

/** A03 case view: evidence checklist, mediated document access, and every decision with a reason. */
export function ProviderCase() {
  const { id = '' } = useParams();
  const c = useLoad(() => api.request<Case>(`/v1/admin/providers/${id}`), [id]);
  const [identity, setIdentity] = useState<Row | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [method, setMethod] = useState('id_document_inspected');
  const d = c.data;

  const decideApp = (decision: string) => async (reason: string) => {
    await api.request(`/v1/admin/providers/${id}/application/decision`, {
      body: { decision, reason, identity_verified: decision === 'approve', age_verification_method: decision === 'approve' ? method : null },
    });
    await c.reload();
  };
  const decideApproval = (approvalId: unknown, decision: string) => async (reason: string) => {
    await api.request(`/v1/admin/provider-approvals/${approvalId}/decision`, { body: { decision, reason } });
    await c.reload();
  };
  const reviewEvidence = (evidenceId: unknown, decision: string) => async (reason: string) => {
    await api.request(`/v1/admin/evidence/${evidenceId}/review`, { body: { decision, reason } });
    await c.reload();
  };

  async function openEvidence(evidenceId: unknown, purpose: string) {
    const blob = await api.downloadEvidence(String(evidenceId), purpose);
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank', 'noopener,noreferrer');
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }

  return (
    <>
      <h1>Provider {id.slice(0, 8)}</h1>
      <ErrorBanner error={c.error || error} />
      {d && (
        <>
          <Section title="Application">
            <dl className="kv">
              <dt>Application</dt><dd><Badge value={d.application_state} /></dd>
              <dt>Account</dt><dd><Badge value={d.account_state} /> {d.suspended_reason && <span className="muted">({d.suspended_reason})</span>}</dd>
              <dt>Identity verified</dt><dd>{fmt(d.identity_verified_at)}</dd>
              <dt>Age verified</dt><dd>{fmt(d.age_verified_at)} {d.age_verification_method && `· ${d.age_verification_method}`}</dd>
            </dl>
            {identity ? (
              <dl className="kv" style={{ marginTop: 12 }}>
                <dt>Legal name</dt><dd>{String(identity.legal_name)}</dd>
                <dt>Date of birth</dt><dd>{String(identity.date_of_birth)} (age {String(identity.age_today)})</dd>
              </dl>
            ) : (
              <ReasonAction label="Show identity details (logged)" onSubmit={async (purpose) =>
                setIdentity(await api.request<Row>(`/v1/admin/providers/${id}/identity-view`, { body: { purpose } }))} />
            )}
            {d.application_state === 'submitted' && <ReasonAction label="Start review" onSubmit={decideApp('start_review')} />}
            {['submitted', 'under_review'].includes(d.application_state) && (
              <ReasonAction label="Ask for more information" onSubmit={decideApp('needs_more_info')} />
            )}
            {d.application_state === 'under_review' && (
              <ReasonAction label="Approve application" onSubmit={decideApp('approve')} extra={(
                <>
                  <label htmlFor="age-method">I verified identity and age against the original ID by</label>
                  <select id="age-method" value={method} onChange={(e) => setMethod(e.target.value)}>
                    <option value="id_document_inspected">inspecting the ID document</option>
                    <option value="in_person_id_check">an in-person ID check</option>
                  </select>
                </>)} />
            )}
            {['submitted', 'under_review'].includes(d.application_state) && (
              <ReasonAction label="Deny application" danger onSubmit={decideApp('deny')} />
            )}
            {d.account_state === 'active'
              ? <ReasonAction label="Suspend provider now" danger onSubmit={async (reason) => {
                await api.request(`/v1/admin/providers/${id}/suspend`, { body: { reason } });
                await c.reload();
              }} />
              : <ReasonAction label="Reinstate provider" onSubmit={async (reason) => {
                await api.request(`/v1/admin/providers/${id}/reinstate`, { body: { reason } });
                await c.reload();
              }} />}
          </Section>

          <Section title="Documents">
            <Notice>Opening a document needs a fresh authenticator code and a purpose. Every view is logged.</Notice>
            <Table rows={d.evidence} empty="No documents uploaded." columns={[
              { key: 'evidence_type', label: 'Type' },
              { key: 'scan_state', label: 'Scan', render: (r) => <Badge value={String(r.scan_state)} /> },
              { key: 'review_state', label: 'Review', render: (r) => <Badge value={String(r.review_state)} /> },
              { key: 'document_expires_on', label: 'Expires' },
              { key: 'next_review_due', label: 'Re-review due' },
              { key: 'actions', label: '', render: (r) => r.scan_state !== 'clean' ? <span className="muted">locked until scan is clean</span> : (
                <>
                  <ReasonAction label="Open document" onSubmit={(purpose) => openEvidence(r.id, purpose).catch((e) => { setError(e); throw e; })} />
                  {r.review_state === 'pending' && (
                    <>
                      <ReasonAction label="Accept" onSubmit={reviewEvidence(r.id, 'accept')} />
                      <ReasonAction label="Needs more info" onSubmit={reviewEvidence(r.id, 'needs_more_info')} />
                      <ReasonAction label="Reject" danger onSubmit={reviewEvidence(r.id, 'reject')} />
                    </>
                  )}
                </>) },
            ]} />
          </Section>

          <Section title="Category approvals">
            <p className="muted small">An approval for one category never covers another. Approval is blocked while any required
              evidence is missing or expired; there is no override.</p>
            <Table rows={d.approvals} empty="No category requests." columns={[
              { key: 'category_code', label: 'Category' },
              { key: 'status', label: 'Status', render: (r) => <Badge value={String(r.status)} /> },
              { key: 'policy_version', label: 'Policy v' },
              { key: 'valid_until', label: 'Valid until' },
              { key: 'eligibility_gaps', label: 'Eligibility gaps', render: (r) => (r.eligibility_gaps as string[]).length
                ? (r.eligibility_gaps as string[]).map((g) => <div key={g}><code>{g}</code></div>) : <Badge value="eligible" /> },
              { key: 'actions', label: '', render: (r) => (
                <>
                  {['pending', 'needs_more_info', 'denied', 'revoked', 'expired'].includes(String(r.status)) &&
                    <ReasonAction label="Start category review" onSubmit={decideApproval(r.id, 'start_review')} />}
                  {r.status === 'under_review' && <ReasonAction label="Approve category" onSubmit={decideApproval(r.id, 'approve')} />}
                  {['pending', 'under_review'].includes(String(r.status)) &&
                    <ReasonAction label="Deny category" danger onSubmit={decideApproval(r.id, 'deny')} />}
                  {r.status === 'approved' && <ReasonAction label="Revoke category" danger onSubmit={decideApproval(r.id, 'revoke')} />}
                </>) },
            ]} />
          </Section>

          <Section title="Application history">
            <Table rows={d.application_history} columns={[{ key: 'created_at', label: 'When' }, { key: 'actor_type', label: 'By' },
              { key: 'from_state', label: 'From' }, { key: 'to_state', label: 'To' }, { key: 'reason', label: 'Reason' }]} />
          </Section>
        </>
      )}
    </>
  );
}
