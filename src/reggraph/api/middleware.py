"""Cross-cutting HTTP protections.

====  ============================================================================
H1    Request body size is capped while it streams (413), not only by Content-Length.
H2    Every response carries security headers (nosniff, no framing, no caching, CSP ...).
H3    Every request gets an id (a client-supplied one is accepted only if it looks safe).
H4    Per-client rate limiting answers 429 with Retry-After.
H5    Any unexpected exception becomes a generic localized 500: no stack trace, no path.
H6    The caller's language is negotiated once and exposed as ``Content-Language``.
H7    The dashboard page (only) gets a CSP that allows its own script and style files and
      nothing else: no inline code, no third-party hosts, no framing.
====  ============================================================================
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Final

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from reggraph.api.deps import Services
from reggraph.core.errors import RateLimitedError, RegGraphError
from reggraph.i18n import negotiate

log = logging.getLogger("reggraph.api")

_REQUEST_ID_RE: Final = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_DOCS_PATHS: Final = ("/docs", "/openapi.json")
_PAGE_PATHS: Final = (
    "/dashboard",
    "/dashboard/app.js",
    "/dashboard/app.css",
    "/dashboard/icon.svg",
)
PAGE_CSP: Final = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)  # no inline script or style, no third parties: the dashboard page loads only its own files

SECURITY_HEADERS: Final[dict[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


class BodySizeLimitMiddleware:
    """Pure ASGI middleware so the limit applies to streamed (chunked) bodies as well."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return
        received = 0
        overflow = False
        responded = False

        async def limited_receive() -> Message:
            nonlocal received, overflow
            if overflow:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    overflow = True  # stop feeding the app; answer 413 ourselves
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal responded
            if overflow:
                if not responded:
                    responded = True
                    await self._reject(send)
                return  # drop whatever the app tried to say
            if message["type"] == "http.response.start":
                responded = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not overflow:
                raise
        if overflow and not responded:
            await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"code":"payload_too_large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def error_response(
    services: Services, request: Request, status: int, code: str, key: str, **extra: Any
) -> JSONResponse:
    locale = getattr(request.state, "locale", "en")
    body: dict[str, Any] = {
        "code": code,
        "message": services.translator.translate(key, locale),
        "request_id": getattr(request.state, "request_id", ""),
        **extra,
    }
    return JSONResponse(body, status_code=status)


def install_middleware(app: FastAPI, services: Services) -> None:
    @app.middleware("http")
    async def protect(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get("x-request-id", "")
        request.state.request_id = supplied if _REQUEST_ID_RE.match(supplied) else uuid.uuid4().hex
        request.state.locale = negotiate(request.headers.get("accept-language"))
        try:
            if request.url.path != "/health":
                client = request.client.host if request.client else "unknown"
                if not services.limiter.allow(client):
                    raise RateLimitedError("rate limit exceeded")
            response = await call_next(request)
        except RateLimitedError as exc:
            response = error_response(services, request, exc.http_status, exc.code, exc.message_key)
            response.headers["Retry-After"] = "5"
        except RegGraphError as exc:  # pragma: no cover - handled by route exception handlers
            response = error_response(services, request, exc.http_status, exc.code, exc.message_key)
        except Exception:
            log.exception("unhandled error request_id=%s", request.state.request_id)
            response = error_response(services, request, 500, "internal_error", "error.internal")
        for name, value in SECURITY_HEADERS.items():
            if name == "Content-Security-Policy" and request.url.path in _DOCS_PATHS:
                continue  # the interactive docs need scripts; they are disabled in prod
            response.headers[name] = value
        if request.url.path in _PAGE_PATHS:
            response.headers["Content-Security-Policy"] = PAGE_CSP
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Content-Language"] = request.state.locale
        return response

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=services.settings.max_body_bytes)
