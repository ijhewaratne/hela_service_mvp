import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Badge, ErrorBanner, Notice, ReasonAction, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;

/** A08 region/category control with maker/checker (Q15): one admin proposes, a different admin approves. */
export function Controls() {
  const { me } = useAuth();
  const regions = useLoad(() => api.request<Row[]>('/v1/admin/regions'));
  const categories = useLoad(() => api.request<Row[]>('/v1/admin/categories'));
  const changes = useLoad(() => api.request<Row[]>('/v1/admin/change-requests'));
  const reloadAll = async () => { await Promise.all([regions.reload(), categories.reload(), changes.reload()]); };

  const propose = (path: string, change_type: string) => async (justification: string) => {
    await api.request(path, { body: { change_type, justification } });
    await changes.reload();
  };
  const decide = (id: unknown, decision: string) => async (reason: string) => {
    await api.request(`/v1/admin/change-requests/${id}/decision`, { body: { decision, reason } });
    await reloadAll();
  };

  return (
    <>
      <h1>Regions &amp; categories</h1>
      <Notice>Every change needs a second administrator to approve it. Childcare and clinical care can never be enabled;
        gated categories need an approved policy first. All synthetic policies are UNAPPROVED.</Notice>
      <ErrorBanner error={regions.error || categories.error || changes.error} />
      <Section title="Pending changes">
        <Table rows={changes.data?.filter((c) => c.status === 'pending')} empty="No pending changes." columns={[
          { key: 'change_type', label: 'Change' }, { key: 'justification', label: 'Justification' },
          { key: 'requested_at', label: 'Requested' },
          { key: 'decide', label: '', render: (c) => c.requested_by === me?.id
            ? <span className="muted">You proposed this; another administrator must decide.</span>
            : (<><ReasonAction label="Approve change" onSubmit={decide(c.id, 'approve')} />
              <ReasonAction label="Reject change" danger onSubmit={decide(c.id, 'reject')} /></>) },
        ]} />
      </Section>
      <Section title="Regions">
        <Table rows={regions.data} columns={[
          { key: 'name', label: 'Region' }, { key: 'status', label: 'Status', render: (r) => <Badge value={String(r.status)} /> },
          { key: 'approval_status', label: 'Boundary approval', render: (r) => <Badge value={String(r.approval_status)} /> },
          { key: 'is_synthetic', label: 'Synthetic' },
          { key: 'propose', label: '', render: (r) => (
            <ReasonAction label={r.status === 'enabled' ? 'Propose disabling' : 'Propose enabling'} minLength={10}
              onSubmit={propose(`/v1/admin/regions/${r.id}/change-requests`, r.status === 'enabled' ? 'region.disable' : 'region.enable')} />) },
        ]} />
      </Section>
      <Section title="Categories">
        <Table rows={categories.data} columns={[
          { key: 'display_name', label: 'Category' }, { key: 'risk_class', label: 'Class' },
          { key: 'status', label: 'Status', render: (r) => <Badge value={String(r.status)} /> },
          { key: 'active_policy_version', label: 'Policy v' },
          { key: 'policy_approval_status', label: 'Policy approval', render: (r) => <Badge value={String(r.policy_approval_status)} /> },
          { key: 'propose', label: '', render: (r) => ['disabled', 'out_of_scope'].includes(String(r.risk_class)) ? <span className="muted">never offered</span> : (
            <ReasonAction label={r.status === 'enabled' ? 'Propose disabling' : 'Propose enabling'} minLength={10}
              onSubmit={propose(`/v1/admin/categories/${r.code}/change-requests`, r.status === 'enabled' ? 'category.disable' : 'category.enable')} />) },
        ]} />
      </Section>
    </>
  );
}
