import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, ErrorBanner, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;

/** A03 queue: oldest first, with age so the verification backlog is visible (§18 verification_backlog_age). */
export function Verification() {
  const queue = useLoad(() => api.request<{ applications: Row[]; category_approvals: Row[]; evidence: Row[] }>(
    '/v1/admin/verification-queue'));
  const expiring = useLoad(() => api.request<{ evidence: Row[]; approvals: Row[] }>('/v1/admin/verification/expiring?days=30'));
  const provider = (r: Row) => <Link to={`/providers/${r.provider_id}`}>{String(r.provider_id).slice(0, 8)}</Link>;
  return (
    <>
      <h1>Provider review</h1>
      <ErrorBanner error={queue.error || expiring.error} />
      <Section title="Applications">
        <Table rows={queue.data?.applications} empty="No applications waiting." columns={[
          { key: 'provider_id', label: 'Provider', render: provider },
          { key: 'application_state', label: 'State', render: (r) => <Badge value={String(r.application_state)} /> },
          { key: 'submitted_at', label: 'Submitted' },
          { key: 'age_hours', label: 'Waiting (h)' },
        ]} />
      </Section>
      <Section title="Category approvals">
        <Table rows={queue.data?.category_approvals} empty="No category requests waiting." columns={[
          { key: 'provider_id', label: 'Provider', render: provider },
          { key: 'category_code', label: 'Category' },
          { key: 'status', label: 'Status', render: (r) => <Badge value={String(r.status)} /> },
          { key: 'age_hours', label: 'Waiting (h)' },
        ]} />
      </Section>
      <Section title="Documents waiting for review">
        <Table rows={queue.data?.evidence} empty="No documents waiting." columns={[
          { key: 'provider_id', label: 'Provider', render: provider },
          { key: 'evidence_type', label: 'Type' },
          { key: 'scan_state', label: 'Malware scan', render: (r) => <Badge value={String(r.scan_state)} /> },
          { key: 'uploaded_at', label: 'Uploaded' },
        ]} />
      </Section>
      <Section title="Expiring within 30 days (reminders only, never auto-renewed)">
        <Table rows={[...(expiring.data?.approvals ?? []).map((r) => ({ ...r, what: `approval: ${r.category_code}`, when: r.valid_until })),
          ...(expiring.data?.evidence ?? []).map((r) => ({ ...r, what: `document: ${r.evidence_type}`,
            when: r.document_expires_on ?? r.next_review_due }))]}
          empty="Nothing expiring soon." columns={[
            { key: 'provider_id', label: 'Provider', render: provider }, { key: 'what', label: 'Item' }, { key: 'when', label: 'Expires / review due' }]} />
      </Section>
    </>
  );
}
