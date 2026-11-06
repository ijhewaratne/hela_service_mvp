import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, ErrorBanner, fmt, Notice, ReasonAction, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;

/** A06 queue: urgent first, then by first-response deadline (targets are UNAPPROVED, Q57). */
export function Incidents() {
  const q = useLoad(() => api.request<Row[]>('/v1/admin/incidents'));
  return (
    <>
      <h1>Incidents</h1>
      <Notice>Response targets shown here are proposed and unapproved (DEC-10). Hela is not an emergency service.</Notice>
      <ErrorBanner error={q.error} />
      <Section title="Queue">
        <Table rows={q.data} empty="No incidents." columns={[
          { key: 'id', label: 'Case', render: (r) => <Link to={`/incidents/${r.id}`}>{String(r.id).slice(0, 8)}</Link> },
          { key: 'severity', label: 'Severity', render: (r) => <Badge value={String(r.severity)} /> },
          { key: 'kind', label: 'Kind' },
          { key: 'status', label: 'Status', render: (r) => <Badge value={String(r.status)} /> },
          { key: 'first_response_due', label: 'First response due' },
          { key: 'response_overdue', label: 'Overdue', render: (r) => (r.response_overdue ? <Badge value="overdue" /> : fmt(false)) },
        ]} />
      </Section>
    </>
  );
}

const ACTIONS: Record<string, string[]> = {
  open: ['acknowledge', 'start_investigation'], acknowledged: ['start_investigation', 'resolve'],
  investigating: ['resolve'], resolved: ['close'], closed: [],
};

/** A06 case: the narrative is decrypted for responders only and every view is logged server-side. */
export function IncidentCase() {
  const { id = '' } = useParams();
  const c = useLoad(() => api.request<Row & { actions: Row[] }>(`/v1/admin/incidents/${id}`), [id]);
  const [version, setVersion] = useState('');
  const d = c.data;
  const act = (action: string, extra: Row = {}) => async (note: string) => {
    await api.request(`/v1/admin/incidents/${id}/actions`, { body: { action, note, ...extra } });
    await c.reload();
  };
  return (
    <>
      <h1>Incident {id.slice(0, 8)}</h1>
      <ErrorBanner error={c.error} />
      {d && (
        <>
          <Section title="Case">
            <dl className="kv">
              <dt>Severity</dt><dd><Badge value={String(d.severity)} /> {d.urgent ? <Badge value="urgent" /> : null}</dd>
              <dt>Kind</dt><dd>{String(d.kind)}</dd>
              <dt>Status</dt><dd><Badge value={String(d.status)} /></dd>
              <dt>Booking</dt><dd>{d.booking_id ? <Link to={`/bookings/${d.booking_id}`}>{String(d.booking_id).slice(0, 8)}</Link> : '—'}</dd>
              <dt>Reported by</dt><dd>{String(d.reporter_type)} <span className="muted">(identity withheld by default)</span></dd>
              <dt>First response due</dt><dd>{fmt(d.first_response_due)} · responded {fmt(d.first_response_at)}</dd>
            </dl>
            <h3>Report</h3>
            <p style={{ whiteSpace: 'pre-wrap' }}>{String(d.description)}</p>
          </Section>
          <Section title="Actions">
            {(ACTIONS[String(d.status)] ?? []).map((a) => <ReasonAction key={a} label={a.replace('_', ' ')} onSubmit={act(a)} />)}
            <ReasonAction label="Assign to me" onSubmit={act('assign_to_me')} />
            <ReasonAction label="Add note" onSubmit={act('note')} />
            {Boolean(d.booking_id) && (
              <ReasonAction label="Place booking on incident hold" danger onSubmit={act('place_booking_on_hold', { booking_version: Number(version) })}
                extra={(<><label htmlFor="bv">Current booking version (from the booking page)</label>
                  <input id="bv" inputMode="numeric" value={version} onChange={(e) => setVersion(e.target.value.replace(/\D/g, ''))} required /></>)} />
            )}
          </Section>
          <Section title="Case history">
            <Table rows={d.actions} columns={[{ key: 'created_at', label: 'When' }, { key: 'action', label: 'Action' }, { key: 'note', label: 'Note' }]} />
          </Section>
        </>
      )}
    </>
  );
}
