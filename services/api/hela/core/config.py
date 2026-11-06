"""Runtime settings. Secrets come from the environment only; nothing secret is committed."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='HELA_', env_file='.env', extra='ignore')

    env: Literal['dev', 'test', 'staging', 'production'] = 'dev'
    database_url: str = 'postgresql+psycopg://hela_app:hela_app_dev_only@127.0.0.1:55432/hela'
    redis_url: str = 'redis://127.0.0.1:56379/0'

    # Token signing and field encryption. Dev defaults are refused outside dev/test.
    jwt_secret: str = Field(default='dev-only-jwt-secret-change-me-0123456789abcdef', min_length=32)
    jwt_issuer: str = 'hela-api'
    access_token_minutes: int = 10
    refresh_token_days: int = 30
    admin_refresh_token_hours: int = 12
    field_key_b64: str = 'ZGV2LW9ubHktZmllbGQta2V5LTMyLWJ5dGVzLXh4eHg='  # 32 bytes, dev only
    lookup_key_b64: str = 'ZGV2LW9ubHktbG9va3VwLWtleS0zMi1ieXRlcy14eHg='  # 32 bytes, dev only

    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_max_sends_per_window: int = 3
    otp_send_window_seconds: int = 900
    otp_ip_limit_per_window: int = 20

    step_up_seconds: int = 300
    admin_mfa_token_seconds: int = 300
    admin_max_failed_logins: int = 5

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

    # Sandbox SMS inbox: only mounted when env == 'dev' and a token is configured.
    dev_inbox_token: str | None = None

    @model_validator(mode='after')
    def refuse_dev_secrets_outside_dev(self):
        if self.env in ('staging', 'production'):
            if 'dev-only' in self.jwt_secret or self.field_key_b64.startswith('ZGV2LW9ubHkt') \
                    or self.lookup_key_b64.startswith('ZGV2LW9ubHkt'):
                raise ValueError('Development secrets are not allowed outside dev/test')
            if 'dev_s3_secret_only' in self.s3_secret_key:
                raise ValueError('Development storage credentials are not allowed outside dev/test')
            if self.dev_inbox_token:
                raise ValueError('The sandbox SMS inbox cannot be enabled outside dev')
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
