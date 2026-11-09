import { Fragment } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, ErrorBanner, fmt, ReasonAction, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;
interface Queue {
  unfulfilled_or_expired: Row[];
  awaiting_beneficiary: Row[];
  searching: Row[];
  bookings_needing_attention: Row[];
  alerts: Row[];
  dead_jobs: Row[];
}

const reqLink = (r: Row) => <Link to={`/requests/${r.id}`}>{String(r.id).slice(0, 8)}</Link>;
const bookingLink = (r: Row) => <Link to={`/bookings/${r.id}`}>{String(r.id).slice(0, 8)}</Link>;

/** A02 operations dashboard + A10 system health. */
export function Dashboard() {
  const queue = useLoad(() => api.request<Queue>('/v1/admin/operations/queue'));
  const health = useLoad(() => api.request<Record<string, string>>('/health'));
  const q = queue.data;

  return (
    <>
      <header className="ops-header">
        <div>
          <h1>Operations</h1>
          <p className="muted">Review outstanding work first. Counts reflect the latest successful API response, not live real-time monitoring.</p>
        </div>
        <button className="btn btn-ghost" type="button" onClick={() => { void queue.reload(); void health.reload(); }}>
          Refresh queues
        </button>
      </header>
      <ErrorBanner error={queue.error || health.error} />
      {queue.error && <p role="status" className="muted">The last queue refresh failed; any visible counts may be stale.</p>}
      <section aria-label="Priorities requiring attention">
        <h2 className="ops-subheading">Review first</h2>
        <div className="triage-grid" aria-live="polite">
          <div className="triage-card triage-urgent">
            <span>Open operational alerts</span>
            <strong data-testid="alerts-count">{q?.alerts.length ?? '—'}</strong>
            <small>Review urgency and responsible team before closing an alert.</small>
          </div>
          <div className="triage-card">
            <span>Bookings needing attention</span>
            <strong data-testid="booking-attention-count">{q?.bookings_needing_attention.length ?? '—'}</strong>
            <small>Disputed, held or awaiting customer confirmation.</small>
          </div>
          <div className="triage-card">
            <span>Requests without a provider</span>
            <strong data-testid="unfulfilled-count">{q?.unfulfilled_or_expired.length ?? '—'}</strong>
            <small>Never describe these requests as confirmed.</small>
          </div>
          <div className="triage-card">
            <span>Awaiting beneficiary consent</span>
            <strong data-testid="consent-count">{q?.awaiting_beneficiary.length ?? '—'}</strong>
            <small>Do not dispatch while authorization is pending.</small>
          </div>
        </div>
      </section>
      <Section title="System health">
        {health.data ? (
          <dl className="kv">
            {Object.entries(health.data).map(([k, v]) => (<Fragment key={k}><dt>{k}</dt><dd><Badge value={v} /></dd></Fragment>))}
            <dt>Dead jobs</dt><dd><Badge value={q ? String(q.dead_jobs.length) : '…'} /></dd>
            <dt>Provider search in progress</dt><dd>{q?.searching.length ?? '—'}</dd>
          </dl>
        ) : <p className="muted">System health is not available yet.</p>}
      </Section>

      <Section title="Alerts needing a person">
        <Table rows={q?.alerts} empty="No open alerts." columns={[
          { key: 'severity', label: 'Severity', render: (r) => <Badge value={String(r.severity)} /> },
          { key: 'kind', label: 'Kind' },
          { key: 'ref', label: 'About', render: (r) => r.ref_type === 'booking'
            ? <Link to={`/bookings/${r.ref_id}`}>booking {String(r.ref_id).slice(0, 8)}</Link>
            : r.ref_type === 'incident' ? <Link to={`/incidents/${r.ref_id}`}>incident {String(r.ref_id).slice(0, 8)}</Link>
              : fmt(r.ref_id) },
          { key: 'created_at', label: 'Raised' },
          { key: 'close', label: '', render: (r) => (
            <ReasonAction label="Close alert" onSubmit={async (reason) => {
              await api.request(`/v1/admin/ops-alerts/${r.id}/close`, { body: { reason } });
              await queue.reload();
            }} />) },
        ]} />
      </Section>

      <Section title="Bookings needing attention">
        <Table rows={q?.bookings_needing_attention} empty="Nothing disputed, on hold or unconfirmed." columns={[
          { key: 'id', label: 'Booking', render: bookingLink },
          { key: 'status', label: 'Status', render: (r) => <Badge value={String(r.status)} /> },
          { key: 'start_at', label: 'Service time' },
          { key: 'completed_pending_since', label: 'Awaiting confirmation since' },
        ]} />
      </Section>

      <Section title="Requests with no provider (honest unfulfilled state)">
        <Table rows={q?.unfulfilled_or_expired} empty="None." columns={[
          { key: 'id', label: 'Request', render: reqLink },
          { key: 'status', label: 'Status', render: (r) => <Badge value={String(r.status)} /> },
          { key: 'category_code', label: 'Category' },
          { key: 'start_at', label: 'Service time' },
        ]} />
      </Section>

      <Section title="Searching / awaiting beneficiary">
        <Table rows={[...(q?.searching ?? []), ...(q?.awaiting_beneficiary ?? []).map((r) => ({ ...r, status: 'awaiting_beneficiary' }))]}
          empty="None." columns={[
            { key: 'id', label: 'Request', render: reqLink },
            { key: 'status', label: 'Status', render: (r) => <Badge value={String(r.status ?? 'offering')} /> },
            { key: 'dispatch_round', label: 'Round' },
            { key: 'start_at', label: 'Service time' },
          ]} />
      </Section>

      {q && q.dead_jobs.length > 0 && (
        <Section title="Dead-lettered background jobs">
          <Table rows={q.dead_jobs} columns={[{ key: 'kind', label: 'Kind' }, { key: 'attempts', label: 'Attempts' },
            { key: 'last_error', label: 'Last error' }, { key: 'updated_at', label: 'When' }]} />
        </Section>
      )}
    </>
  );
}
