"""HTTP hardening (ASVS V3/V8/V13/V14): response security headers, no-store caching for personal data, and request
body size limits enforced on the streamed bytes (Content-Length alone can be absent or wrong)."""
from __future__ import annotations

import json

from .config import Settings
from .errors import envelope

DOCS_PATHS = ('/docs', '/openapi.json')


class SecurityMiddleware:
    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings
        self.upload_limit = settings.max_evidence_bytes + 64 * 1024   # multipart overhead
        self.json_limit = settings.max_json_body_bytes

    def _limit(self, path: str) -> int:
        return self.upload_limit if path == '/v1/provider/evidence' else self.json_limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path, limit = scope['path'], self._limit(scope['path'])
        declared = dict(scope.get('headers') or []).get(b'content-length')
        if declared is not None and declared.isdigit() and int(declared) > limit:
            return await self._too_large(send)
        received = 0
        too_large = False

        async def guarded_receive():
            nonlocal received, too_large
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > limit:
                    too_large = True
                    # Stop reading: hand the app an empty final chunk; the response is replaced below.
                    return {'type': 'http.request', 'body': b'', 'more_body': False}
            return message

        started = False

        async def secured_send(message):
            nonlocal started
            if too_large and not started:
                started = True
                await self._too_large(send)
                return
            if too_large:
                return
            if message['type'] == 'http.response.start':
                started = True
                headers = [(k, v) for k, v in message.get('headers', [])]
                names = {k.lower() for k, _ in headers}
                extra = [(b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'no-referrer'),
                         (b'x-frame-options', b'DENY'), (b'permissions-policy', b'camera=(), microphone=(), geolocation=()'),
                         (b'cross-origin-resource-policy', b'same-origin')]
                if not path.startswith(DOCS_PATHS):
                    extra.append((b'content-security-policy', b"default-src 'none'; frame-ancestors 'none'"))
                if b'cache-control' not in names and path.startswith('/v1'):
                    extra.append((b'cache-control', b'no-store'))
                if self.settings.env in ('staging', 'production'):
                    extra.append((b'strict-transport-security', b'max-age=31536000; includeSubDomains'))
                message = {**message, 'headers': headers + [h for h in extra if h[0] not in names]}
            await send(message)

        await self.app(scope, guarded_receive, secured_send)

    @staticmethod
    async def _too_large(send):
        body = json.dumps(envelope('PAYLOAD_TOO_LARGE', 'error.payload_too_large')).encode()
        await send({'type': 'http.response.start', 'status': 413,
                    'headers': [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode()),
                                (b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff')]})
        await send({'type': 'http.response.body', 'body': body})
