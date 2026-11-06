import { api } from '../api/client';
import { Badge, ErrorBanner, ReasonAction, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;

/** FR-REV-01 moderation: text is held until a person approves it; likely phone/email leaks are flagged first. */
export function Reviews() {
  const q = useLoad(() => api.request<Row[]>('/v1/admin/reviews/pending'));
  const decide = (id: unknown, decision: string) => async (reason: string) => {
    await api.request(`/v1/admin/reviews/${id}/moderate`, { body: { decision, reason } });
    await q.reload();
  };
  return (
    <>
      <h1>Reviews awaiting moderation</h1>
      <ErrorBanner error={q.error} />
      <Section title="Pending">
        <Table rows={q.data} empty="No reviews waiting." columns={[
          { key: 'rating', label: 'Rating' },
          { key: 'pii_flag', label: 'Possible personal data', render: (r) => (r.pii_flag ? <Badge value="flagged" /> : 'no') },
          { key: 'text', label: 'Text' },
          { key: 'actions', label: '', render: (r) => (<>
            <ReasonAction label="Publish" onSubmit={decide(r.id, 'approve')} />
            <ReasonAction label="Reject" danger onSubmit={decide(r.id, 'reject')} />
          </>) },
        ]} />
      </Section>
    </>
  );
}
