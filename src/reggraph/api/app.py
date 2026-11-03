"""Application factory. Wires settings, services, middleware and error handlers."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from reggraph import __version__
from reggraph.api.deps import Services, build_services
from reggraph.api.middleware import error_response, install_middleware
from reggraph.api.routes import router
from reggraph.config import Settings
from reggraph.core.clock import Clock
from reggraph.core.errors import AuthenticationError, RegGraphError
from reggraph.netio import Fetcher

_HTTP_KEYS = {404: "error.not_found", 405: "error.not_found", 403: "error.forbidden"}


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    *,
    connector_fetcher: Fetcher | None = None,
    oidc_fetcher: Fetcher | None = None,
    services: Services | None = None,
) -> FastAPI:
    cfg = settings or Settings.from_env()
    services = services or build_services(
        cfg, clock, connector_fetcher=connector_fetcher, oidc_fetcher=oidc_fetcher
    )
    app = FastAPI(
        title="RegGraph Europe",
        version=__version__,
        docs_url="/docs" if cfg.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if cfg.docs_enabled else None,
    )
    app.state.services = services
    app.include_router(router)
    install_middleware(app, services)

    @app.exception_handler(RegGraphError)
    async def domain_error(request: Request, exc: RegGraphError) -> JSONResponse:
        response = error_response(services, request, exc.http_status, exc.code, exc.message_key)
        if isinstance(exc, AuthenticationError):
            response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Only the location and rule type are returned: never the submitted value.
        problems = [{"loc": [str(p) for p in e["loc"]], "type": e["type"]} for e in exc.errors()]
        return error_response(
            services, request, 422, "invalid_input", "error.invalid_input", problems=problems
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        key = _HTTP_KEYS.get(exc.status_code, "error.invalid_input")
        return error_response(services, request, exc.status_code, "http_error", key)

    return app
