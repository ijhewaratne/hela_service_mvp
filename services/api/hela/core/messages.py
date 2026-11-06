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
    'catalog.category_forbidden': 'This service category cannot be offered.',
    'catalog.policy_missing': 'This category has no active policy version.',
    'catalog.policy_not_approved': 'This category policy has not been approved.',
    'provider.not_found': 'No provider application was found.',
    'provider.underage': 'Providers must meet the minimum age for this service.',
    'provider.already_applied': 'You have already applied as a provider.',
    'provider.upload_not_allowed': 'Documents cannot be uploaded at this stage.',
    'provider.document_expired': 'This document has already expired.',
    'provider.file_rejected': 'This file was not accepted. Upload a PDF, JPEG or PNG under the size limit.',
    'provider.not_submittable': 'This application cannot be submitted now.',
    'provider.evidence_required': 'Upload at least one document before submitting.',
    'provider.category_not_allowed': 'This service category is not available.',
    'provider.category_already_requested': 'You have already requested this category.',
    'verification.evidence_not_clean': 'This document has not passed malware scanning.',
    'verification.already_decided': 'This item has already been decided.',
    'verification.transition_not_allowed': 'This decision is not allowed in the current state.',
    'verification.attestation_required': 'Confirm identity and age verification before approving.',
    'verification.identity_evidence_required': 'An accepted identity document is required.',
    'verification.gates_not_met': 'Required evidence is missing, expired or not accepted.',
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
