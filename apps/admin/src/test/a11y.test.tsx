import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import axe from 'axe-core';
import { ReactNode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '../auth/AuthContext';
import { CategoryPolicies } from '../pages/CategoryPolicies';
import { Controls } from '../pages/Controls';
import { Dashboard } from '../pages/Dashboard';
import { Incidents } from '../pages/Incidents';
import { Login } from '../pages/Login';
import { Privacy } from '../pages/Privacy';
import { Settlements } from '../pages/Settlements';
import { Verification } from '../pages/Verification';

/** Automated WCAG checks with axe-core on each console page, with representative synthetic data.
 *  Colour contrast cannot be computed in jsdom, so it is checked in the browser instead (see docs). */
const id = '0d8f6a52-3c1e-4c55-9d3e-6f1b2a7c9e10';
const DATA: [RegExp, unknown][] = [
  [/\/v1\/admin\/operations\/queue/, { unfulfilled_or_expired: [], awaiting_beneficiary: [], bookings_needing_attention: [],
    alerts: [], dead_jobs: [], searching: [{ id, category_code: 'household_cleaning', status: 'offering', start_at: '2026-11-08T04:30:00Z' }] }],
  [/\/health$/, { status: 'ok', database: 'ok', cache: 'ok' }],
  [/\/v1\/admin\/verification\/expiring/, { evidence: [], approvals: [] }],
  [/\/v1\/admin\/verification/, { applications: [{ id, display_name: 'Provider 001', application_state: 'submitted' }],
    category_approvals: [], evidence: [] }],
  [/\/v1\/admin\/incidents$/, [{ id, kind: 'safety', severity: 'high', status: 'open', urgent: true, overdue: false,
    first_response_due: '2026-11-08T04:30:00Z' }]],
  [/\/v1\/admin\/privacy-requests$/, [{ id, kind: 'correction', status: 'received', due_at: '2026-12-01T00:00:00Z', overdue: false }]],
  [/\/v1\/admin\/retention/, { active_legal_holds: 0, policies: [{ data_category: 'auth_logs', purpose: 'Security', lawful_basis: 'proposed',
    retention_days: null, approval_status: 'UNAPPROVED', rows: 3, action: 'report only' }] }],
  [/\/v1\/admin\/categories\/household_cleaning\/policies/, { price_rules: [{ id, version: 1, status: 'active', hourly_rate_minor: 120000,
    base_fee_minor: 0, approval_status: 'UNAPPROVED' }], policies: [{ id, version: 1, status: 'active', approval_status: 'UNAPPROVED',
    allowed_tasks: ['dusting'], prohibited_tasks: ['clinical_tasks'], min_duration_minutes: 60, max_duration_minutes: 480,
    max_radius_km: 10, required_evidence: [{ type: 'national_id', count: 1 }], safeguards_required: false }] }],
  [/\/v1\/admin\/regions$/, [{ id, code: 'synthetic_kandy_central', name: 'Synthetic Kandy', status: 'enabled', approval_status: 'UNAPPROVED' }]],
  [/\/v1\/admin\/categories$/, [{ code: 'household_cleaning', display_name: 'Household cleaning', status: 'enabled', risk_class: 'pilot_candidate' }]],
  [/\/v1\/admin\/change-requests/, []],
  [/\/v1\/admin\/settlements/, [{ booking_id: id, status: 'DISPUTED', amount_minor: 240000, platform_collected_funds: false }]],
];

beforeEach(() => {
  vi.stubGlobal('fetch', async (url: string) => {
    const hit = DATA.find(([re]) => re.test(String(url)));
    return new Response(JSON.stringify(hit ? hit[1] : {}), { status: hit ? 200 : 404, headers: { 'content-type': 'application/json' } });
  });
});
afterEach(() => vi.unstubAllGlobals());

async function violations(ui: ReactNode, path = '/', route = '/', wrapInMain = true) {
  const { container } = render(
    <AuthProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path={route} element={wrapInMain ? <main>{ui}</main> : ui} /></Routes>
      </MemoryRouter>
    </AuthProvider>,
  );
  await screen.findByRole('heading', { level: 1 });
  await new Promise((r) => setTimeout(r, 50));       // let the page's data requests settle
  const result = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  return result.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(' ')).join('; ')})`);
}

describe('console pages pass automated accessibility checks', () => {
  it.each([
    ['operations', () => <Dashboard />],
    ['provider review', () => <Verification />],
    ['incidents', () => <Incidents />],
    ['settlements', () => <Settlements />],
    ['regions and categories', () => <Controls />],
    ['privacy', () => <Privacy />],
  ])('%s', async (_name, page) => {
    expect(await violations(page())).toEqual([]);
  });

  it('offers supervised recovery information without public admin account creation', async () => {
    render(
      <AuthProvider>
        <MemoryRouter><Login /></MemoryRouter>
      </AuthProvider>,
    );
    expect(screen.queryByRole('region', { name: 'Administrator account recovery' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Forgot password or lost authenticator?' }));
    expect(screen.getByRole('region', { name: 'Administrator account recovery' }))
      .toHaveTextContent('supervised password or authenticator reset');
    expect(screen.queryByText('Create administrator account')).not.toBeInTheDocument();
  });

  it('sign in (has its own main landmark)', async () => {
    expect(await violations(<Login />, '/', '/', false)).toEqual([]);
  });

  it('category policies', async () => {
    expect(await violations(<CategoryPolicies />, '/controls/household_cleaning', '/controls/:code')).toEqual([]);
  });
  it('shows counts derived from the operations queue without suggesting synthetic requests are confirmed', async () => {
    render(
      <AuthProvider>
        <MemoryRouter><main><Dashboard /></main></MemoryRouter>
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('alerts-count')).toHaveTextContent('0'));
    expect(screen.getByTestId('booking-attention-count')).toHaveTextContent('0');
    expect(screen.getByTestId('unfulfilled-count')).toHaveTextContent('0');
    expect(screen.getByTestId('consent-count')).toHaveTextContent('0');
    expect(screen.getByText('Never describe these requests as confirmed.')).toBeVisible();
  });

});
