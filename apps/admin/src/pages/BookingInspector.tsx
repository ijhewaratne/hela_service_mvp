import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, ErrorBanner, fmt, lkr, ReasonAction, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;
interface Booking {
  booking_id: string; status: string; version: number; category_code: string; start_at: string; end_at: string;
  pin_attempts: number; events: Row[]; assignments: Row[];
  settlement: { status: string; agreed_amount_minor: number; resolution: string | null } | null;
}

// Admin transitions allowed by the server state machine (spec §7.2). The server re-checks all of them.
const ADMIN_MOVES: Record<string, string[]> = {
  accepted: ['cancelled', 'incident_hold'], en_route: ['cancelled', 'incident_hold'], arrived: ['cancelled', 'incident_hold'],
  in_progress: ['incident_hold'], completed_pending_confirmation: ['completed', 'disputed'],
  disputed: ['completed', 'cancelled_after_review'], incident_hold: ['disputed', 'cancelled_after_review'],
  reassigning: ['cancelled'],
};

/** A05 booking inspector: timeline, assignments and audited interventions. No address or contact details. */
export function BookingInspector() {
  const { id = '' } = useParams();
  const b = useLoad(() => api.request<Booking>(`/v1/admin/bookings/${id}`), [id]);
  const [target, setTarget] = useState('');
  const d = b.data;
  const moves = d ? ADMIN_MOVES[d.status] ?? [] : [];
  return (
    <>
      <h1>Booking {id.slice(0, 8)}</h1>
      <ErrorBanner error={b.error} />
      {d && (
        <>
          <Section title="Booking">
            <dl className="kv">
              <dt>Status</dt><dd><Badge value={d.status} /> <span className="muted">version {d.version}</span></dd>
              <dt>Category</dt><dd>{d.category_code}</dd>
              <dt>Service time</dt><dd>{fmt(d.start_at)} – {fmt(d.end_at)}</dd>
              <dt>Failed check-in codes</dt><dd>{d.pin_attempts}</dd>
              <dt>Settlement</dt><dd>{d.settlement ? <><Badge value={d.settlement.status} /> {lkr(d.settlement.agreed_amount_minor)} · Hela holds no funds</> : '—'}</dd>
            </dl>
            {moves.length > 0 && (
              <ReasonAction label={target ? `Move to ${target}` : 'Change status'} danger={target.startsWith('cancel')}
                onSubmit={async (reason) => {
                  await api.request(`/v1/admin/bookings/${id}/transitions`, { body: { target_status: target, expected_version: d.version, reason }, idempotent: true });
                  setTarget('');
                  await b.reload();
                }}
                extra={(<><label htmlFor="target">New status</label>
                  <select id="target" value={target} onChange={(e) => setTarget(e.target.value)} required>
                    <option value="">Choose…</option>{moves.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select></>)} />
            )}
            {['accepted', 'en_route'].includes(d.status) && (
              <ReasonAction label="Reassign to another provider" onSubmit={async (reason) => {
                await api.request(`/v1/admin/bookings/${id}/reassign`, { body: { reason, expected_version: d.version } });
                await b.reload();
              }} />
            )}
            {d.status === 'arrived' && (
              <ReasonAction label="Record check-in exception" onSubmit={async (reason) => {
                await api.request(`/v1/admin/bookings/${id}/checkin-exception`, { body: { reason, expected_version: d.version } });
                await b.reload();
              }} />
            )}
          </Section>
          <Section title="Assignments">
            <Table rows={d.assignments} columns={[{ key: 'provider_id', label: 'Provider', render: (r) => String(r.provider_id).slice(0, 8) },
              { key: 'active', label: 'Live' }, { key: 'started_at', label: 'From' }, { key: 'ended_at', label: 'Until' }, { key: 'end_reason', label: 'Ended because' }]} />
          </Section>
          <Section title="Timeline">
            <Table rows={d.events} columns={[{ key: 'created_at', label: 'When' }, { key: 'actor_type', label: 'By' },
              { key: 'from_status', label: 'From' }, { key: 'to_status', label: 'To' }, { key: 'version', label: 'v' }, { key: 'reason', label: 'Reason' }]} />
          </Section>
        </>
      )}
    </>
  );
}
