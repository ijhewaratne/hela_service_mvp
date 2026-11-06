// Server message keys -> English. The API also returns an English `message`; the console prefers its own copy so
// operations wording can be reviewed in one place. The admin console is English-only in R2 (staff-facing).
const EN: Record<string, string> = {
  'auth.admin_login_failed': 'Sign-in failed. Check the username and password.',
  'auth.admin_locked': 'This account is locked. Ask a super-administrator to unlock it.',
  'auth.mfa_invalid': 'That authenticator code was not accepted. Wait for the next code and try again.',
  'auth.session_idle': 'You were signed out after 30 minutes of inactivity.',
  'auth.step_up_required': 'This action needs a fresh authenticator code.',
  'authz.permission_denied': 'Your role does not allow this action.',
  'verification.gates_not_met': 'Approval blocked: required evidence is missing, expired or not accepted.',
};

export function t(key: string, fallback: string): string {
  return EN[key] ?? fallback;
}
