"""Message keys. English is the reviewed source; si/ta are UNTRANSLATED until human review (Q61)."""
from __future__ import annotations

SUPPORTED_LANGUAGES = ('si', 'ta', 'en')

EN: dict[str, str] = {
    'auth.required': 'Sign in to continue.',
    'auth.invalid_token': 'Your session is invalid or has expired. Sign in again.',
    'auth.session_revoked': 'This session has been signed out.',
    'auth.account_inactive': 'This account is not active. Contact support.',
    'auth.otp_sent': 'If this number can receive messages, a code has been sent.',
    'auth.otp_invalid': 'The code is incorrect or has expired.',
    'auth.otp_locked': 'Too many attempts. Request a new code.',
    'auth.refresh_invalid': 'Your session has expired. Sign in again.',
    'auth.refresh_reused': 'This session was signed out for your security. Sign in again.',
    'auth.admin_login_failed': 'Sign-in failed.',
    'auth.admin_locked': 'This administrator account is locked. Contact a super-administrator.',
    'auth.mfa_invalid': 'The authenticator code is incorrect.',
    'auth.step_up_required': 'Confirm your identity again to perform this action.',
    'authz.permission_denied': 'You do not have permission to perform this action.',
    'notice.not_found': 'This notice is not available.',
    'error.bad_request': 'The request could not be processed.',
    'error.forbidden': 'You do not have access to this resource.',
    'error.not_found': 'Not found.',
    'error.method_not_allowed': 'This action is not supported here.',
    'error.rate_limited': 'Too many requests. Wait and try again.',
    'error.validation': 'Some fields are missing or invalid.',
    'error.unexpected': 'Something went wrong. Try again later.',
    'error.dependency_unavailable': 'A required service is temporarily unavailable. Try again later.',
}

# Placeholders: development fallback only. A live release needs human-reviewed translations.
UNTRANSLATED = {'si': {}, 'ta': {}}


def english(key: str) -> str:
    return EN.get(key, key)
