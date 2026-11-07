"""FastAPI application factory for the Hela Service R1 API."""
from __future__ import annotations

import redis
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .audit.routes import router as audit_router
from .authz.admin_routes import router as access_router
from .booking import admin as booking_admin
from .booking import availability, bookings, settlement
from .booking import requests as booking_requests
from .catalog import policies as catalog_policies
from .catalog.regions import admin_router as region_admin_router
from .catalog.regions import public as region_public_router
from .core import errors
from .core.config import Settings, get_settings
from .core.db import make_engine
from .core.http import SecurityMiddleware
from .core.services import Services, services
from .customers import routes as customer_routes
from .evidence.scanning import ClamdScanner, NoScanner
from .evidence.storage import EvidenceSealer, S3ObjectStore
from .identity import admin_routes, dev_inbox
from .identity import routes as identity_routes
from .identity.sms import SandboxInboxSender, UnconfiguredSender
from .privacy import routes as privacy_routes
from .safety import incidents
from .security.crypto import EnvKeyProvider, FieldCipher
from .verification import admin_routes as verification_admin_routes
from .verification import provider_routes


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title='Hela Service API (R1, synthetic development only)', version='1.0.0.dev0',
                  description='Not approved for real users, real data or public deployment.',
                  docs_url='/docs' if settings.env in ('dev', 'test') else None, redoc_url=None)
    sandbox = settings.env in ('dev', 'test')
    cipher = FieldCipher(EnvKeyProvider(settings.field_key_b64, settings.lookup_key_b64))
    app.state.services = Services(
        settings=settings,
        engine=make_engine(settings.database_url),
        cipher=cipher,
        redis=redis.Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2),
        sms=SandboxInboxSender() if sandbox else UnconfiguredSender(),
        store=S3ObjectStore(endpoint=settings.s3_endpoint, access_key=settings.s3_access_key,
                            secret_key=settings.s3_secret_key, bucket=settings.s3_bucket),
        sealer=EvidenceSealer(cipher),
        scanner=ClamdScanner(settings.clamd_host, settings.clamd_port) if settings.clamd_host else NoScanner(),
    )
    errors.install(app)
    app.add_middleware(SecurityMiddleware, settings=settings)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                           allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'],
                           allow_headers=['Authorization', 'Content-Type', 'Idempotency-Key', 'X-Correlation-ID'],
                           expose_headers=['X-Correlation-ID'], max_age=600)

    @app.get('/health', tags=['ops'])
    def health(svc: Services = Depends(services)):
        status = {'database': 'ok', 'cache': 'ok'}
        try:
            with svc.engine.connect() as con:
                con.execute(text('SELECT 1'))
        except Exception:
            status['database'] = 'unavailable'
        try:
            svc.redis.ping()
        except redis.RedisError:
            status['cache'] = 'unavailable'
        overall = 'ok' if all(v == 'ok' for v in status.values()) else 'degraded'
        return {'status': overall, **status}

    for router in (identity_routes.router, admin_routes.router, access_router, region_public_router,
                   region_admin_router, audit_router, provider_routes.router, verification_admin_routes.router,
                   customer_routes.router, availability.router, booking_requests.router, bookings.router,
                   settlement.router, incidents.router, booking_admin.router, catalog_policies.router,
                   privacy_routes.router):
        app.include_router(router)
    if settings.env == 'dev' and settings.dev_inbox_token:
        app.include_router(dev_inbox.router)
    return app
