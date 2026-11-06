import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, ErrorBanner, fmt, ReasonAction, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;

/** A04 dispatch console: why each provider was or was not offered the job, without the household address. */
export function RequestInspector() {
  const { id = '' } = useParams();
  const r = useLoad(() => api.request<Row & { matching_runs: Row[]; offers: Row[]; events: Row[] }>(`/v1/admin/requests/${id}`), [id]);
  const d = r.data;
  return (
    <>
      <h1>Request {id.slice(0, 8)}</h1>
      <ErrorBanner error={r.error} />
      {d && (
        <>
          <Section title="Request">
            <dl className="kv">
              <dt>Status</dt><dd><Badge value={String(d.status)} /></dd>
              <dt>Category</dt><dd>{String(d.category_code)}</dd>
              <dt>Service time</dt><dd>{fmt(d.start_at)} – {fmt(d.end_at)}</dd>
              <dt>Tasks</dt><dd>{(d.task_codes as string[]).join(', ')}</dd>
              <dt>For a beneficiary</dt><dd>{fmt(d.for_beneficiary)}</dd>
              <dt>Dispatch round</dt><dd>{String(d.dispatch_round)}</dd>
            </dl>
            {['unfulfilled', 'requested', 'offering'].includes(String(d.status)) && (
              <ReasonAction label="Retry dispatch" onSubmit={async (reason) => {
                await api.request(`/v1/admin/requests/${id}/dispatch`, { body: { reason } });
                await r.reload();
              }} />
            )}
            {String(d.status) === 'awaiting_beneficiary' && (
              <ReasonAction label="Record staff-assisted consent" onSubmit={async (reason) => {
                await api.request(`/v1/admin/requests/${id}/beneficiary-consent`, { body: { reason } });
                await r.reload();
              }} />
            )}
          </Section>
          <Section title="Matching runs (config version, exclusion reason counts, ranking)">
            {(d.matching_runs as Row[]).map((m) => (
              <div key={String(m.id)} className="card">
                <div className="row"><strong>Round {String(m.round)}</strong><Badge value={String(m.outcome)} />
                  <span className="muted">{String(m.config_version)} · {String(m.candidate_count)} candidates · {String(m.eligible_count)} eligible</span></div>
                <Table rows={Object.entries(m.exclusions as Record<string, number>).map(([code, n]) => ({ id: code, code, n }))}
                  empty="No exclusions." columns={[{ key: 'code', label: 'Excluded because' }, { key: 'n', label: 'Providers' }]} />
                <Table rows={(m.ranked as Row[]).map((x, i) => ({ ...x, id: `${x.provider_id}-${i}`, rank: i + 1 }))} empty="Nobody passed every gate."
                  columns={[{ key: 'rank', label: '#' }, { key: 'provider_id', label: 'Provider', render: (x) => <Link to={`/providers/${x.provider_id}`}>{String(x.provider_id).slice(0, 8)}</Link> },
                    { key: 'rank_cost', label: 'Rank cost (lower first)' }, { key: 'components', label: 'Components' }]} />
              </div>
            ))}
          </Section>
          <Section title="Offers">
            <Table rows={d.offers as Row[]} columns={[{ key: 'provider_id', label: 'Provider', render: (x) => String(x.provider_id).slice(0, 8) },
              { key: 'status', label: 'Status', render: (x) => <Badge value={String(x.status)} /> }, { key: 'distance_band', label: 'Distance' },
              { key: 'expires_at', label: 'Expires' }, { key: 'responded_at', label: 'Responded' }]} />
          </Section>
          <Section title="History">
            <Table rows={d.events as Row[]} columns={[{ key: 'created_at', label: 'When' }, { key: 'actor_type', label: 'By' },
              { key: 'from_status', label: 'From' }, { key: 'to_status', label: 'To' }, { key: 'reason', label: 'Reason' }]} />
          </Section>
        </>
      )}
    </>
  );
}
