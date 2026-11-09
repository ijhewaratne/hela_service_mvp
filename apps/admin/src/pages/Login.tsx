import { FormEvent, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { ErrorBanner, Notice } from '../components/ui';

/** A01: username + password, then authenticator code. No "remember me": tokens never leave memory. */
export function Login() {
  const { signIn, completeMfa, expired } = useAuth();
  const [step, setStep] = useState<'password' | 'mfa'>('password');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [mfaToken, setMfaToken] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [showRecovery, setShowRecovery] = useState(false);

  async function submitPassword(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setMfaToken(await signIn(username.trim().toLowerCase(), password));
      setPassword('');
      setStep('mfa');
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function submitMfa(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await completeMfa(mfaToken, code);
    } catch (err) {
      setError(err);
      setCode('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <h1>Hela Operations</h1>
      <Notice>Synthetic development environment. Not for real users or real personal data.</Notice>
      {expired && <Notice>Your session ended. Sign in again.</Notice>}
      <div className="card">
        {step === 'password' ? (
          <form onSubmit={submitPassword}>
            <label htmlFor="username">Username</label>
            <input id="username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
            <label htmlFor="password">Password</label>
            <input id="password" type="password" autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} required />
            <div className="row"><button className="btn" disabled={busy}>{busy ? 'Checking…' : 'Continue'}</button></div>
          </form>
        ) : (
          <form onSubmit={submitMfa}>
            <label htmlFor="totp">Authenticator code</label>
            <input id="totp" ref={(el) => el?.focus()} inputMode="numeric" autoComplete="one-time-code" maxLength={6} pattern="[0-9]{6}" value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} required />
            <div className="row">
              <button type="button" className="btn btn-ghost" onClick={() => setStep('password')}>Back</button>
              <button className="btn" disabled={busy || code.length !== 6}>{busy ? 'Checking…' : 'Sign in'}</button>
            </div>
          </form>
        )}
        <ErrorBanner error={error} />
        <div className="row">
          <button type="button" className="btn btn-ghost"
            aria-expanded={showRecovery}
            aria-controls="admin-account-recovery"
            onClick={() => setShowRecovery((visible) => !visible)}>
            Forgot password or lost authenticator?
          </button>
        </div>
        {showRecovery && (
          <div id="admin-account-recovery" role="region" aria-label="Administrator account recovery">
            <Notice>
              Administrator accounts are issued by Hela Operations. Contact your designated
              system administrator through your organization's verified support channel to
              request a supervised password or authenticator reset. Self-service account
              creation and recovery are not enabled. Never share passwords, recovery codes
              or authenticator secrets.
            </Notice>
          </div>
        )}
      </div>
    </main>
  );
}
