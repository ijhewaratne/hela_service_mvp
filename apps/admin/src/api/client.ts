// Admin API client. Tokens live only in memory (never localStorage/cookies): a page reload signs the admin out,
// which is the intended trade-off for a privileged console (Q73). Bearer headers mean no CSRF surface.

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8000';

export interface ApiErrorBody {
  code: string;
  message_key: string;
  message: string;
  correlation_id: string;
  retryable: boolean;
  details: Record<string, unknown>;
}

export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(body.message);
  }
  get code() {
    return this.body.code;
  }
}

export interface Tokens {
  access_token: string;
  refresh_token: string;
  session_id: string;
}

type StepUpHandler = (action: string) => Promise<boolean>;

interface RequestOptions {
  method?: string;
  body?: unknown;
  idempotent?: boolean; // add a fresh Idempotency-Key
  raw?: boolean; // return the Response (downloads)
  retriedStepUp?: boolean;
}

export class AdminApi {
  private tokens: Tokens | null = null;
  private refreshing: Promise<boolean> | null = null;
  onStepUp: StepUpHandler | null = null;
  onSignedOut: (() => void) | null = null;

  constructor(private base = API_BASE, private fetcher: typeof fetch = (...a) => fetch(...a)) {}

  get signedIn() {
    return this.tokens !== null;
  }

  setTokens(t: Tokens | null) {
    this.tokens = t;
  }

  async request<T>(path: string, opts: RequestOptions = {}, refreshed = false): Promise<T> {
    const res = await this.send(path, opts);
    if (!res.ok) {
      const err = await toError(res);
      // Only an expired/invalid access token is worth one refresh; MFA_INVALID etc. are plain errors.
      if (err.code === 'TOKEN_INVALID' && this.tokens && !refreshed && (await this.refresh())) {
        return this.request<T>(path, opts, true);
      }
      if (['SESSION_REVOKED', 'SESSION_IDLE_TIMEOUT', 'TOKEN_INVALID', 'ACCOUNT_INACTIVE'].includes(err.code) && this.tokens) {
        this.signOutLocally();
      }
      if (err.code === 'STEP_UP_REQUIRED' && this.onStepUp && !opts.retriedStepUp) {
        const action = String(err.body.details.action ?? '');
        if (await this.onStepUp(action)) return this.request<T>(path, { ...opts, retriedStepUp: true }, refreshed);
      }
      throw err;
    }
    if (opts.raw) return res as unknown as T;
    if (res.status === 204) return undefined as T;
    const type = res.headers.get('content-type') ?? '';
    return (type.includes('application/json') ? res.json() : res.text()) as Promise<T>;
  }

  private send(path: string, opts: RequestOptions) {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (this.tokens) headers.Authorization = `Bearer ${this.tokens.access_token}`;
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    if (opts.idempotent) headers['Idempotency-Key'] = `admin-${crypto.randomUUID()}`;
    return this.fetcher(`${this.base}${path}`, {
      method: opts.method ?? (opts.body === undefined ? 'GET' : 'POST'),
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      credentials: 'omit',
      cache: 'no-store',
    });
  }

  /** One refresh at a time: concurrent 401s share it, so a rotated token is never replayed (reuse detection). */
  private refresh(): Promise<boolean> {
    if (!this.refreshing) {
      const current = this.tokens;
      this.refreshing = (async () => {
        if (!current) return false;
        const res = await this.fetcher(`${this.base}/v1/admin/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: current.refresh_token }),
          credentials: 'omit',
        });
        if (!res.ok) {
          this.signOutLocally();
          return false;
        }
        this.tokens = (await res.json()) as Tokens;
        return true;
      })().finally(() => {
        this.refreshing = null;
      });
    }
    return this.refreshing;
  }

  private signOutLocally() {
    this.tokens = null;
    this.onSignedOut?.();
  }

  // --- auth -----------------------------------------------------------------------------------------------
  async login(username: string, password: string): Promise<{ mfa_token: string }> {
    return this.request('/v1/admin/auth/login', { body: { username, password } });
  }

  async mfa(mfaToken: string, totp: string): Promise<void> {
    const t = await this.request<Tokens>('/v1/admin/auth/mfa', { body: { mfa_token: mfaToken, totp, device_label: 'admin-web' } });
    this.tokens = t;
  }

  async stepUp(action: string, totp: string): Promise<void> {
    await this.request('/v1/admin/auth/step-up', { body: { action, totp }, retriedStepUp: true });
  }

  async logout(): Promise<void> {
    try {
      await this.request('/v1/admin/auth/logout', { method: 'POST' });
    } finally {
      this.tokens = null;
    }
  }

  /** Evidence: one-time grant (step-up + purpose), then fetch the bytes with the bearer token. */
  async downloadEvidence(evidenceId: string, purpose: string): Promise<Blob> {
    const grant = await this.request<{ download_path: string }>(`/v1/admin/evidence/${evidenceId}/access-grant`, {
      body: { purpose },
    });
    const res = await this.request<Response>(grant.download_path, { raw: true });
    return res.blob();
  }
}

async function toError(res: Response): Promise<ApiError> {
  try {
    const data = await res.json();
    if (data && data.error) return new ApiError(res.status, data.error as ApiErrorBody);
  } catch {
    /* non-JSON error */
  }
  return new ApiError(res.status, {
    code: `HTTP_${res.status}`,
    message_key: 'error.unexpected',
    message: 'Something went wrong. Try again later.',
    correlation_id: res.headers.get('X-Correlation-ID') ?? '-',
    retryable: res.status >= 500,
    details: {},
  });
}

export const api = new AdminApi();
