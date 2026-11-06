import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AdminApi, ApiError } from '../api/client';

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}
const envelope = (code: string, details = {}) => ({
  error: { code, message_key: 'x', message: code, correlation_id: 'c_test', retryable: false, details },
});

describe('AdminApi', () => {
  it('sends bearer tokens, never cookies, and parses the error envelope', async () => {
    const calls: RequestInit[] = [];
    const api = new AdminApi('http://api', async (_u, init) => { calls.push(init!); return json(403, envelope('PERMISSION_DENIED')); });
    api.setTokens({ access_token: 'A1', refresh_token: 'R1', session_id: 's' });
    await expect(api.request('/v1/admin/audit')).rejects.toMatchObject({ code: 'PERMISSION_DENIED', status: 403 });
    expect((calls[0].headers as Record<string, string>).Authorization).toBe('Bearer A1');
    expect(calls[0].credentials).toBe('omit');
  });

  it('refreshes once on TOKEN_INVALID and shares one refresh across concurrent calls', async () => {
    let refreshes = 0;
    const api = new AdminApi('http://api', async (url, init) => {
      const auth = (init?.headers as Record<string, string>)?.Authorization;
      if (String(url).endsWith('/refresh')) {
        refreshes += 1;
        return json(200, { access_token: 'A2', refresh_token: 'R2', session_id: 's' });
      }
      return auth === 'Bearer A2' ? json(200, { ok: true }) : json(401, envelope('TOKEN_INVALID'));
    });
    api.setTokens({ access_token: 'A1', refresh_token: 'R1', session_id: 's' });
    const results = await Promise.all([api.request('/a'), api.request('/b'), api.request('/c')]);
    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }]);
    expect(refreshes).toBe(1); // a rotated refresh token is never replayed (server would revoke the session)
  });

  it('does not refresh or loop on a wrong authenticator code', async () => {
    let n = 0;
    const api = new AdminApi('http://api', async () => { n += 1; return json(401, envelope('MFA_INVALID')); });
    api.setTokens({ access_token: 'A1', refresh_token: 'R1', session_id: 's' });
    await expect(api.stepUp('evidence.read', '123456')).rejects.toBeInstanceOf(ApiError);
    expect(n).toBe(1);
    expect(api.signedIn).toBe(true);
  });

  it('signs out locally when the session is revoked or idle', async () => {
    const api = new AdminApi('http://api', async () => json(401, envelope('SESSION_IDLE_TIMEOUT')));
    let signedOut = false;
    api.onSignedOut = () => { signedOut = true; };
    api.setTokens({ access_token: 'A1', refresh_token: 'R1', session_id: 's' });
    await expect(api.request('/v1/admin/auth/me')).rejects.toMatchObject({ code: 'SESSION_IDLE_TIMEOUT' });
    expect(signedOut).toBe(true);
    expect(api.signedIn).toBe(false);
  });

  it('asks for step-up and retries the original call exactly once', async () => {
    const seen: string[] = [];
    let stepped = false;
    const api = new AdminApi('http://api', async (url) => {
      seen.push(String(url));
      if (String(url).endsWith('/step-up')) { stepped = true; return json(201, { action: 'evidence.read' }); }
      return stepped ? json(200, { legal_name: 'X' }) : json(403, envelope('STEP_UP_REQUIRED', { action: 'evidence.read' }));
    });
    api.setTokens({ access_token: 'A1', refresh_token: 'R1', session_id: 's' });
    api.onStepUp = async (action) => { expect(action).toBe('evidence.read'); await api.stepUp(action, '123456'); return true; };
    await expect(api.request('/v1/admin/providers/p/identity-view', { body: { purpose: 'check' } })).resolves.toEqual({ legal_name: 'X' });
    expect(seen.filter((u) => u.endsWith('identity-view'))).toHaveLength(2);
  });

  it('adds a fresh Idempotency-Key only when asked', async () => {
    const keys: (string | undefined)[] = [];
    const api = new AdminApi('http://api', async (_u, init) => {
      keys.push((init!.headers as Record<string, string>)['Idempotency-Key']);
      return json(200, {});
    });
    await api.request('/x', { body: {}, idempotent: true });
    await api.request('/x', { body: {}, idempotent: true });
    await api.request('/y');
    expect(keys[0]).toMatch(/^admin-/);
    expect(keys[0]).not.toBe(keys[1]);
    expect(keys[2]).toBeUndefined();
  });
});

describe('navigation', () => {
  it('shows only modules the signed-in admin is permitted to use', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    const { AuthProvider } = await import('../auth/AuthContext');
    const { App } = await import('../App');
    globalThis.fetch = (async (url: string) => {
      if (url.endsWith('/login')) return json(200, { mfa_token: 'm' });
      if (url.endsWith('/mfa')) return json(200, { access_token: 'A', refresh_token: 'R', session_id: 's' });
      if (url.endsWith('/auth/me')) {
        return json(200, { id: 'a1', username: 'audit.bob', last_login_at: null,
          permissions: { 'audit.read': ['*'], 'incident.respond': ['*'] } });
      }
      if (url.includes('/incidents')) return json(200, []);
      return json(200, { items: [], next_before_id: null, intact: true, rows_checked: 0, unsealed_rows: 0 });
    }) as typeof fetch;
    const user = userEvent.setup();
    render(<MemoryRouter><AuthProvider><App /></AuthProvider></MemoryRouter>);
    await user.type(screen.getByLabelText('Username'), 'Audit.Bob');
    await user.type(screen.getByLabelText('Password'), 'synthetic-password');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.type(await screen.findByLabelText('Authenticator code'), '123456');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    const nav = await screen.findByRole('navigation', { name: 'Modules' });
    const links = Array.from(nav.querySelectorAll('a')).map((a) => a.textContent);
    expect(links).toEqual(['Incidents', 'Audit']);           // no Operations, Provider review, Settlements, ...
    expect(screen.getByText('audit.bob')).toBeInTheDocument();
  });
});
