import { useState } from 'react';
import { api } from '../api/client';
import { Badge, ErrorBanner, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;

/** A09 audit search with chain verification. Details are already stripped of secrets server-side. */
export function Audit() {
  const [action, setAction] = useState('');
  const [query, setQuery] = useState('');
  const page = useLoad(() => api.request<{ items: Row[]; next_before_id: number | null }>(
    `/v1/admin/audit?limit=100${query ? `&action=${encodeURIComponent(query)}` : ''}`), [query]);
  const verify = useLoad(() => api.request<Row>('/v1/admin/audit/verify'));
  return (
    <>
      <h1>Audit</h1>
      <ErrorBanner error={page.error || verify.error} />
      <Section title="Tamper evidence" actions={<button className="btn btn-ghost" onClick={() => void verify.reload()}>Verify again</button>}>
        {verify.data && (
          <p><Badge value={verify.data.intact ? 'intact' : 'BROKEN'} /> {String(verify.data.rows_checked)} sealed rows,
            {' '}{String(verify.data.unsealed_rows)} awaiting sealing
            {verify.data.first_inconsistent_id ? ` · first inconsistent id ${String(verify.data.first_inconsistent_id)}` : ''}</p>
        )}
      </Section>
      <Section title="Events">
        <form className="row" onSubmit={(e) => { e.preventDefault(); setQuery(action.trim()); }}>
          <label htmlFor="action" className="sr-only">Filter by action</label>
          <input id="action" placeholder="Filter by exact action, e.g. provider.suspended" value={action}
            onChange={(e) => setAction(e.target.value)} style={{ maxWidth: 360 }} />
          <button className="btn btn-ghost">Filter</button>
        </form>
        <Table rows={page.data?.items} columns={[
          { key: 'occurred_at', label: 'When' }, { key: 'actor_type', label: 'Actor' }, { key: 'action', label: 'Action' },
          { key: 'entity_type', label: 'Entity' }, { key: 'entity_id', label: 'Id', render: (r) => <code>{String(r.entity_id).slice(0, 12)}</code> },
          { key: 'details', label: 'Details' }, { key: 'correlation_id', label: 'Ref' },
        ]} />
      </Section>
    </>
  );
}
