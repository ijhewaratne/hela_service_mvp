import { FormEvent, useEffect, useRef, useState } from 'react';
import { errorText } from './ui';

const ACTION_LABELS: Record<string, string> = {
  'evidence.read': 'view sensitive evidence or identity details',
  'category.approve': 'approve a provider for a category',
  'role.grant': 'change administrator roles',
  'policy.change': 'change regions, categories or policies',
  'provider.reinstate': 'reinstate a suspended provider',
  'checkin.override': 'record a check-in exception',
  'consent.staff': 'record staff-assisted beneficiary consent',
};

/** Modal step-up (Q14): a fresh authenticator code, scoped to one action, valid for five minutes. */
export function StepUpDialog({ action, onSubmit, onCancel }: {
  action: string;
  onSubmit: (totp: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [code, setCode] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit(code);
    } catch (err) {
      setError(err);
      setCode('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onKeyDown={(e) => e.key === 'Escape' && onCancel()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="stepup-title">
        <h2 id="stepup-title">Confirm it’s you</h2>
        <p>Enter a new code from your authenticator app to {ACTION_LABELS[action] ?? action}.</p>
        <form onSubmit={submit}>
          <label htmlFor="stepup-code">Authenticator code</label>
          <input id="stepup-code" ref={input} inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}"
            maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} required />
          {error ? <div className="banner banner-error" role="alert">{errorText(error)}</div> : null}
          <div className="row">
            <button type="button" className="btn btn-ghost" onClick={onCancel}>Cancel</button>
            <button type="submit" className="btn" disabled={busy || code.length !== 6}>{busy ? 'Checking…' : 'Confirm'}</button>
          </div>
        </form>
        <p className="muted small">Each code works once. If it is refused, wait for the next code.</p>
      </div>
    </div>
  );
}
