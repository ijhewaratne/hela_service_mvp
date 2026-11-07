import { useState } from 'react';
import { api } from '../api/client';
import { Badge, ErrorBanner, fmt, Notice, ReasonAction, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;
const NEXT: Record<string, string[]> = {
  received: ['verify_identity', 'hold', 'reject'], identity_verified: ['start', 'hold', 'reject'],
  in_progress: ['fulfil', 'hold', 'reject'], on_legal_hold: ['reject'],
};

/** Privacy requests, legal holds and the retention register (Q63, Q66). Nothing here deletes data automatically. */
export function Privacy() {
  const queue = useLoad(() => api.request<Row[]>('/v1/admin/privacy-requests'));
  const retention = useLoad(() => api.request<{ policies: Row[]; active_legal_holds: number }>('/v1/admin/retention'));
  const [open, setOpen] = useState<Row | null>(null);
  const [holdType, setHoldType] = useState('user');
  const [holdId, setHoldId] = useState('');
  const [error, setError] = useState<unknown>(null);

  async function openCase(id: unknown) {
    setError(null);
    try {
      setOpen(await api.request<Row>(`/v1/admin/privacy-requests/${id}`));
    } catch (e) {
      setError(e);
    }
  }

  return (
    <>
      <h1>Privacy</h1>
      <Notice>Response targets are UNAPPROVED placeholders pending legal review (§15.1). Deletion and correction are done
        manually after checking for legal holds; this console never deletes data by itself.</Notice>
      <ErrorBanner error={queue.error || retention.error || error} />
      <Section title="Requests">
        <Table rows={queue.data} empty="No privacy requests." columns={[
          { key: 'id', label: 'Case', render: (r) => <button className="btn btn-ghost" onClick={() => void openCase(r.id)}>{String(r.id).slice(0, 8)}</button> },
          { key: 'kind', label: 'Kind' },
          { key: 'status', label: 'Status', render: (r) => <Badge value={String(r.status)} /> },
          { key: 'due_at', label: 'Target (unapproved)' },
          { key: 'overdue', label: 'Overdue', render: (r) => (r.overdue ? <Badge value="overdue" /> : fmt(false)) },
        ]} />
      </Section>
      {open && (
        <Section title={`Case ${String(open.id).slice(0, 8)} · ${String(open.kind)}`} actions={<button className="btn btn-ghost" onClick={() => setOpen(null)}>Close</button>}>
          <dl className="kv">
            <dt>Status</dt><dd><Badge value={String(open.status)} /></dd>
            <dt>Account</dt><dd><code>{String(open.user_id)}</code></dd>
            <dt>Details</dt><dd>{fmt(open.details)}</dd>
            <dt>Active holds</dt><dd>{(open.legal_holds_on_user as Row[]).length || 'none'}</dd>
          </dl>
          {(NEXT[String(open.status)] ?? []).map((a) => (
            <ReasonAction key={a} label={a.replace('_', ' ')} danger={a === 'reject'} onSubmit={async (note) => {
              await api.request(`/v1/admin/privacy-requests/${open.id}/actions`, { body: { action: a, note } });
              await openCase(open.id);
              await queue.reload();
            }} />
          ))}
          <Table rows={(open.events as Row[]).map((e, i) => ({ ...e, id: i }))} columns={[
            { key: 'at', label: 'When' }, { key: 'actor_type', label: 'By' }, { key: 'to', label: 'Status' }, { key: 'note', label: 'Note' }]} />
        </Section>
      )}
      <Section title="Legal holds">
        <p className="muted small">A hold blocks deletion of the record. Releasing a hold needs a different administrator.</p>
        <label htmlFor="ht">Record type</label>
        <select id="ht" value={holdType} onChange={(e) => setHoldType(e.target.value)}>
          {['user', 'booking', 'incident', 'provider', 'service_request'].map((t) => <option key={t}>{t}</option>)}
        </select>
        <label htmlFor="hid">Record id</label>
        <input id="hid" value={holdId} onChange={(e) => setHoldId(e.target.value.trim())} placeholder="uuid" />
        <ReasonAction label="Place legal hold" minLength={10} onSubmit={async (reason) => {
          await api.request('/v1/admin/legal-holds', { body: { entity_type: holdType, entity_id: holdId, reason } });
          await retention.reload();
        }} />
        <p>Active holds: <strong>{retention.data?.active_legal_holds ?? '…'}</strong></p>
      </Section>
      <Section title="Retention register (report only)">
        <Table rows={retention.data?.policies.map((p) => ({ ...p, id: p.data_category }))} columns={[
          { key: 'data_category', label: 'Data' }, { key: 'purpose', label: 'Purpose' }, { key: 'lawful_basis', label: 'Proposed basis' },
          { key: 'retention_days', label: 'Period (days)', render: (r) => r.retention_days ? String(r.retention_days) : <span className="muted">not decided</span> },
          { key: 'approval_status', label: 'Approval', render: (r) => <Badge value={String(r.approval_status)} /> },
          { key: 'rows', label: 'Rows' }, { key: 'action', label: 'Action' },
        ]} />
      </Section>
    </>
  );
}
