import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from '../api/client';
import { StepUpDialog } from '../components/StepUpDialog';

export interface AdminMe {
  id: string;
  username: string;
  last_login_at: string | null;
  permissions: Record<string, string[]>; // permission -> region ids, '*' = all regions
}

interface AuthState {
  me: AdminMe | null;
  can: (permission: string) => boolean;
  signIn: (username: string, password: string) => Promise<string>; // returns mfa token
  completeMfa: (mfaToken: string, totp: string) => Promise<void>;
  signOut: () => Promise<void>;
  expired: boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<AdminMe | null>(null);
  const [expired, setExpired] = useState(false);
  const [stepUp, setStepUp] = useState<{ action: string } | null>(null);
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  useEffect(() => {
    api.onSignedOut = () => {
      setMe(null);
      setExpired(true);
    };
    // Server says "confirm again": ask for a fresh TOTP, then the client retries the original call once.
    api.onStepUp = (action) =>
      new Promise<boolean>((resolve) => {
        resolver.current = resolve;
        setStepUp({ action });
      });
    return () => {
      api.onSignedOut = null;
      api.onStepUp = null;
    };
  }, []);

  const can = useCallback((p: string) => Boolean(me?.permissions[p]?.length), [me]);

  const value = useMemo<AuthState>(
    () => ({
      me,
      can,
      expired,
      signIn: async (username, password) => (await api.login(username, password)).mfa_token,
      completeMfa: async (mfaToken, totp) => {
        await api.mfa(mfaToken, totp);
        setMe(await api.request<AdminMe>('/v1/admin/auth/me'));
        setExpired(false);
      },
      signOut: async () => {
        await api.logout();
        setMe(null);
      },
    }),
    [me, can, expired],
  );

  const finish = (ok: boolean) => {
    resolver.current?.(ok);
    resolver.current = null;
    setStepUp(null);
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
      {stepUp && (
        <StepUpDialog
          action={stepUp.action}
          onCancel={() => finish(false)}
          onSubmit={async (totp) => {
            try {
              await api.stepUp(stepUp.action, totp);
              finish(true);
            } catch (e) {
              throw e instanceof ApiError ? e : new Error(String(e));
            }
          }}
        />
      )}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth outside AuthProvider');
  return ctx;
}
