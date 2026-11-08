"""Runtime settings. Secrets come from the environment only; nothing secret is committed."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='HELA_', env_file='.env', extra='ignore')

    env: Literal['dev', 'test', 'staging', 'production'] = 'dev'
    database_url: str = 'postgresql+psycopg://hela_app:hela_app_dev_only@127.0.0.1:55432/hela'
    redis_url: str = 'redis://127.0.0.1:56379/0'

    # Token signing and field encryption (see hela/security/keys.py). Dev defaults are refused outside dev/test.
    key_provider: Literal['local'] = 'local'    # a managed KMS provider is required before staging (Q65)
    jwt_keyring: str | None = None              # JSON {"active", "keys", "verify_only"}; Ed25519 seeds, base64
    jwt_issuer: str = 'hela-api'
    access_token_minutes: int = 10
    refresh_token_days: int = 30
    admin_refresh_token_hours: int = 12
    field_key_b64: str = 'ZGV2LW9ubHktZmllbGQta2V5LTMyLWJ5dGVzLXh4eHg='  # 32 bytes, dev only; key id 'k1'
    field_keyring: str | None = None            # JSON {"active", "keys"}; replaces field_key_b64 when set
    lookup_key_b64: str = 'ZGV2LW9ubHktbG9va3VwLWtleS0zMi1ieXRlcy14eHg='  # 32 bytes, dev only; key id 'k1'
    lookup_keyring: str | None = None           # JSON {"active", "keys"}; replaces lookup_key_b64 when set

    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_max_sends_per_window: int = 3
    otp_send_window_seconds: int = 900
    otp_ip_limit_per_window: int = 20

    step_up_seconds: int = 300
    admin_mfa_token_seconds: int = 300
    admin_max_failed_logins: int = 5
    admin_idle_minutes: int = 30
    max_json_body_bytes: int = 64 * 1024

    # Private evidence storage (S3-compatible) and malware scanning (Q28, Q38)
    s3_endpoint: str = 'http://127.0.0.1:59000'
    s3_access_key: str = 'hela_dev_s3'
    s3_secret_key: str = 'hela_dev_s3_secret_only'  # noqa: S105 - dev default, refused outside dev/test
    s3_bucket: str = 'hela-evidence-dev'
    clamd_host: str | None = '127.0.0.1'
    clamd_port: int = 53310
    max_evidence_bytes: int = 10 * 1024 * 1024
    evidence_token_seconds: int = Field(default=60, le=60)
    min_provider_age: int = Field(default=18, ge=18)  # proposed pilot rule, pending local approval (Q13)

    # Booking engine (synthetic defaults, Q25, Q27, Q41-Q43, Q47-Q49, Q57). Tuning required before any pilot.
    min_notice_minutes: int = 120
    max_advance_days: int = 30
    duration_step_minutes: int = 30
    quote_ttl_seconds: int = 900
    offer_ttl_seconds: int = 600
    max_dispatch_rounds: int = 5
    parallel_offers: int = Field(default=1, ge=1, le=3)
    travel_buffer_minutes: int = 30
    request_expiry_lead_minutes: int = 60
    consent_ttl_minutes: int = 24 * 60
    completion_review_hours: int = 48
    pin_max_attempts: int = 5
    matching_config_version: str = 'synthetic-v1'
    local_timezone: str = 'Asia/Colombo'

    # Browser origins allowed to call the API (the R2 admin web). No credentials/cookies are used: tokens are
    # sent as Bearer headers, so CSRF does not apply (Q73). Keep this list explicit; never '*'.
    cors_origins: list[str] = ['http://localhost:5173', 'http://127.0.0.1:5173']

    # Prometheus scrape endpoint (/internal/metrics). Not mounted unless a token is set (§17).
    metrics_token: str | None = Field(default=None, min_length=24)
    log_format: Literal['json', 'text'] | None = None   # default: text in dev/test, JSON elsewhere
    log_level: Literal['debug', 'info', 'warning', 'error'] = 'info'

    # Sandbox SMS inbox: only mounted when env == 'dev' and a token is configured.
    dev_inbox_token: str | None = None

    @model_validator(mode='after')
    def refuse_dev_secrets_outside_dev(self):
        from ..security.keys import field_keyring, lookup_keyring, signing_keys
        # Malformed key configuration fails at start-up.
        jwt_keys, field_keys, lookup_keys = signing_keys(self), field_keyring(self), lookup_keyring(self)
        if self.env in ('staging', 'production'):
            if jwt_keys.dev_material or field_keys.has_dev_material() or lookup_keys.has_dev_material():
                raise ValueError('Development secrets are not allowed outside dev/test')
            if 'dev_s3_secret_only' in self.s3_secret_key:
                raise ValueError('Development storage credentials are not allowed outside dev/test')
            if self.dev_inbox_token:
                raise ValueError('The sandbox SMS inbox cannot be enabled outside dev')
        return self


    def effective_jwt_keyring(self) -> str:
        if self.jwt_keyring:
            return self.jwt_keyring
        return json.dumps({'active': 'dev1', 'keys': {'dev1': _DEV_JWT_SEED}})


_DEV_JWT_SEED = 'ZGV2LW9ubHktand0LWVkMjU1MTktc2VlZC0zMmJ5dGU='  # b'dev-only-jwt-ed25519-seed-32byte'


@lru_cache
def get_settings() -> Settings:
    return Settings()
