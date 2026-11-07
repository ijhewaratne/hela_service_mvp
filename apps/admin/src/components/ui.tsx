import { FormEvent, ReactNode, useCallback, useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { t } from '../i18n/messages';

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const details = e.body.details as { reason_codes?: string[] };
    const extra = details.reason_codes?.length ? ` (${details.reason_codes.join(', ')})` : '';
    return `${t(e.body.message_key, e.body.message)}${extra} · ${e.code} · ref ${e.body.correlation_id}`;
  }
  return String(e);
}

export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="banner banner-error" role="alert">
      {errorText(error)}
    </div>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  return <div className="banner banner-info">{children}</div>;
}

/** Load data on mount; `reload` re-runs after an action. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await load());
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    void run();
  }, [run]);
  return { data, error, loading, reload: run };
}

export function Badge({ value }: { value: string | null | undefined }) {
  const v = value ?? '—';
  const tone = /urgent|critical|disputed|infected|dead|denied|revoked|suspended|incident_hold|unfulfilled|expired/i.test(v)
    ? 'bad'
    : /pending|warning|offering|under_review|needs_more_info|reassigning|high/i.test(v)
      ? 'warn'
      : /approved|clean|accepted|completed|sent|intact|enabled|resolved|active|ok/i.test(v)
        ? 'good'
        : 'neutral';
  return <span className={`badge badge-${tone}`}>{v}</span>;
}

export function Table({ columns, rows, empty = 'Nothing here.' }: {
  columns: { key: string; label: string; render?: (row: Record<string, unknown>) => ReactNode }[];
  rows: Record<string, unknown>[] | null | undefined;
  empty?: string;
}) {
  if (!rows?.length) return <p className="muted">{empty}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          {/* Columns of buttons have no visible heading; screen readers still get one. */}
          <tr>{columns.map((c) => <th key={c.key} scope="col">{c.label || <span className="sr-only">Actions</span>}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={String(r.id ?? i)}>
              {columns.map((c) => <td key={c.key}>{c.render ? c.render(r) : fmt(r[c.key])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function fmt(v: unknown): ReactNode {
  if (v === null || v === undefined || v === '') return <span className="muted">—</span>;
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(v)) return new Date(v).toLocaleString('en-GB', { timeZone: 'Asia/Colombo' });
  if (typeof v === 'object') return <code>{JSON.stringify(v)}</code>;
  return String(v);
}

export function lkr(minor: number | null | undefined): string {
  if (minor === null || minor === undefined) return '—';
  return `LKR ${(minor / 100).toLocaleString('en-LK', { minimumFractionDigits: 2 })}`;
}

/** Every privileged action carries a written reason (§3.1). The button stays disabled until it is long enough. */
export function ReasonAction({ label, onSubmit, minLength = 5, danger = false, extra }: {
  label: string;
  onSubmit: (reason: string) => Promise<unknown>;
  minLength?: number;
  danger?: boolean;
  extra?: ReactNode;
}) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState(false);
  const id = `reason-${label.replace(/\W+/g, '-').toLowerCase()}`;
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit(reason.trim());
      setDone(true);
      setReason('');
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="reason-action" onSubmit={submit}>
      <label htmlFor={id}>Reason for “{label}”</label>
      <textarea id={id} value={reason} onChange={(e) => { setReason(e.target.value); setDone(false); }} rows={2}
        required minLength={minLength} />
      {extra}
      <button type="submit" className={danger ? 'btn btn-danger' : 'btn'} disabled={busy || reason.trim().length < minLength}>
        {busy ? 'Working…' : label}
      </button>
      {done && <span className="ok" role="status">Done</span>}
      <ErrorBanner error={error} />
    </form>
  );
}

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="card">
      <header className="card-head">
        <h2>{title}</h2>
        {actions}
      </header>
      {children}
    </section>
  );
}
