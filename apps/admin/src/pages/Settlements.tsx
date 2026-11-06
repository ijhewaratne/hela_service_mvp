import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, ErrorBanner, lkr, Notice, ReasonAction, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;

/** A07 settlement desk. Offline payments only: Hela never holds money (INV-10); commission is a 0% placeholder. */
export function Settlements() {
  const [status, setStatus] = useState('DISPUTED');
  const desk = useLoad(() => api.request<Row[]>(`/v1/admin/settlements?status=${status}`), [status]);
  const [resolution, setResolution] = useState('paid');
  const [error, setError] = useState<unknown>(null);

  async function exportCsv() {
    setError(null);
    try {
      const res = await api.request<Response>('/v1/admin/finance/export.csv', { raw: true });
      const url = URL.createObjectURL(await res.blob());
      const a = Object.assign(document.createElement('a'), { href: url, download: 'settlements.csv' });
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e);
    }
  }

  return (
    <>
      <h1>Settlements</h1>
      <Notice>Customer and provider statements are evidence, not proof. Hela does not collect or hold payments.</Notice>
      <ErrorBanner error={desk.error || error} />
      <Section title="Settlement desk" actions={<button className="btn btn-ghost" onClick={() => void exportCsv()}>Export redacted CSV</button>}>
        <label htmlFor="st">Show</label>
        <select id="st" value={status} onChange={(e) => setStatus(e.target.value)}>
          {['DISPUTED', 'CUSTOMER_REPORTED_PAID', 'PROVIDER_CONFIRMED', 'UNPAID', 'RESOLVED'].map((s) => <option key={s}>{s}</option>)}
        </select>
        <Table rows={desk.data?.map((r) => ({ ...r, id: r.booking_id }))} empty="Nothing in this state." columns={[
          { key: 'booking_id', label: 'Booking', render: (r) => <Link to={`/bookings/${r.booking_id}`}>{String(r.booking_id).slice(0, 8)}</Link> },
          { key: 'status', label: 'Status', render: (r) => <Badge value={String(r.status)} /> },
          { key: 'agreed_amount_minor', label: 'Agreed', render: (r) => lkr(r.agreed_amount_minor as number) },
          { key: 'category_code', label: 'Category' },
          { key: 'start_at', label: 'Service date' },
          { key: 'resolve', label: '', render: (r) => r.status !== 'DISPUTED' ? null : (
            <ReasonAction label="Resolve dispute" minLength={10} onSubmit={async (evidence_note) => {
              await api.request(`/v1/admin/settlements/${r.booking_id}/resolve`, { body: { resolution, evidence_note } });
              await desk.reload();
            }} extra={(<><label htmlFor={`res-${r.booking_id}`}>Finding from the evidence</label>
              <select id={`res-${r.booking_id}`} value={resolution} onChange={(e) => setResolution(e.target.value)}>
                <option value="paid">Customer paid the provider</option><option value="unpaid">Payment was not made</option>
              </select></>)} />) },
        ]} />
      </Section>
    </>
  );
}
