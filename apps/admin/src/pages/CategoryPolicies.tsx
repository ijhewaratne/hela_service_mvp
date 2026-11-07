import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, ErrorBanner, lkr, Notice, ReasonAction, Section, Table, useLoad } from '../components/ui';

type Row = Record<string, unknown>;
const list = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean);

/** Policy and price versions for one category (spec §4.2, AT-027). Drafting creates a maker/checker request;
 *  another administrator publishes it from "Regions & categories". */
export function CategoryPolicies() {
  const { code = '' } = useParams();
  const v = useLoad(() => api.request<{ policies: Row[]; price_rules: Row[] }>(`/v1/admin/categories/${code}/policies`), [code]);
  const active = v.data?.policies.find((p) => p.status === 'active');
  const [form, setForm] = useState({ allowed: '', prohibited: '', min: '60', max: '480', radius: '10', evidence: '', safeguards: false });
  const [rate, setRate] = useState('1200');
  const [base, setBase] = useState('0');

  function fromActive() {
    if (!active) return;
    setForm({
      allowed: (active.allowed_tasks as string[]).join(', '), prohibited: (active.prohibited_tasks as string[]).join(', '),
      min: String(active.min_duration_minutes), max: String(active.max_duration_minutes), radius: String(active.max_radius_km),
      evidence: (active.required_evidence as Row[]).map((e) => `${e.type}:${e.count ?? 1}`).join(', '),
      safeguards: Boolean(active.safeguards_required),
    });
  }

  const field = (key: keyof typeof form, label: string, hint?: string) => (
    <>
      <label htmlFor={`f-${key}`}>{label}</label>
      <input id={`f-${key}`} value={String(form[key])} onChange={(e) => setForm({ ...form, [key]: e.target.value })} aria-describedby={hint ? `h-${key}` : undefined} />
      {hint && <div id={`h-${key}`} className="muted small">{hint}</div>}
    </>
  );

  return (
    <>
      <h1>Policy versions · {code.replace(/_/g, ' ')}</h1>
      <p><Link to="/controls">← Regions &amp; categories</Link></p>
      <Notice>Published versions are immutable. A new draft takes effect only after a different administrator approves the
        change request, and stays UNAPPROVED until business/legal sign-off is recorded by an operator.</Notice>
      <ErrorBanner error={v.error} />
      <Section title="Policy versions">
        <Table rows={v.data?.policies} columns={[
          { key: 'version', label: 'v' }, { key: 'status', label: 'Status', render: (r) => <Badge value={String(r.status)} /> },
          { key: 'approval_status', label: 'Sign-off', render: (r) => <Badge value={String(r.approval_status)} /> },
          { key: 'allowed_tasks', label: 'Allowed tasks', render: (r) => (r.allowed_tasks as string[]).join(', ') },
          { key: 'required_evidence', label: 'Evidence', render: (r) => (r.required_evidence as Row[]).map((e) => `${e.type}×${e.count ?? 1}`).join(', ') },
          { key: 'dur', label: 'Duration', render: (r) => `${r.min_duration_minutes}–${r.max_duration_minutes} min` },
          { key: 'max_radius_km', label: 'Radius km' },
        ]} />
      </Section>
      <Section title="Draft a new policy version" actions={<button className="btn btn-ghost" onClick={fromActive}>Start from active version</button>}>
        {field('allowed', 'Allowed tasks', 'Comma-separated task codes, e.g. dusting, tidying')}
        {field('prohibited', 'Prohibited tasks', 'Must include medication_administration and clinical_tasks')}
        {field('min', 'Minimum minutes')}
        {field('max', 'Maximum minutes')}
        {field('radius', 'Maximum service radius (km)')}
        {field('evidence', 'Required evidence', 'type:count, e.g. national_id:1, reference:2, training_record:1')}
        <label><input type="checkbox" checked={form.safeguards} style={{ width: 'auto', minHeight: 0 }}
          onChange={(e) => setForm({ ...form, safeguards: e.target.checked })} /> Enhanced safeguards required</label>
        <ReasonAction label="Submit draft for approval" minLength={10} onSubmit={async (justification) => {
          await api.request(`/v1/admin/categories/${code}/policy-drafts`, { body: {
            allowed_tasks: list(form.allowed), prohibited_tasks: list(form.prohibited),
            min_duration_minutes: Number(form.min), max_duration_minutes: Number(form.max), max_radius_km: Number(form.radius),
            required_evidence: list(form.evidence).map((e) => { const [type, count] = e.split(':'); return { type: type.trim(), count: Number(count ?? 1) }; }),
            safeguards_required: form.safeguards, justification,
          } });
          await v.reload();
        }} />
      </Section>
      <Section title="Price rules (synthetic until DEC-06)">
        <Table rows={v.data?.price_rules} columns={[
          { key: 'version', label: 'v' }, { key: 'status', label: 'Status', render: (r) => <Badge value={String(r.status)} /> },
          { key: 'hourly_rate_minor', label: 'Hourly', render: (r) => lkr(r.hourly_rate_minor as number) },
          { key: 'base_fee_minor', label: 'Base fee', render: (r) => lkr(r.base_fee_minor as number) },
          { key: 'travel_fee_minor', label: 'Travel fee', render: (r) => lkr(r.travel_fee_minor as number) },
        ]} />
        <label htmlFor="rate">Hourly rate (LKR)</label>
        <input id="rate" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
        <label htmlFor="base">Base fee (LKR)</label>
        <input id="base" inputMode="decimal" value={base} onChange={(e) => setBase(e.target.value)} />
        <ReasonAction label="Submit price draft for approval" minLength={10} onSubmit={async (justification) => {
          await api.request(`/v1/admin/categories/${code}/price-drafts`, { body: {
            hourly_rate_minor: Math.round(Number(rate) * 100), base_fee_minor: Math.round(Number(base) * 100), justification,
          } });
          await v.reload();
        }} />
      </Section>
    </>
  );
}
