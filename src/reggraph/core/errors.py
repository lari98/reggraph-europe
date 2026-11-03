"""Domain errors.

Every error carries an HTTP status, a stable machine code and an i18n key, so the
API can answer in the caller's language without leaking internals.

=====================  ======  ====================  ========================
Error                  HTTP    machine code          i18n key
=====================  ======  ====================  ========================
ValidationFailure      422     invalid_input         error.invalid_input
AuthenticationError    401     unauthenticated       error.unauthenticated
ForbiddenError         403     forbidden             error.forbidden
NotFoundError          404     not_found             error.not_found
ConflictError          409     conflict              error.conflict
RateLimitedError       429     rate_limited          error.rate_limited
RateUnavailableError   422     rate_unavailable      error.invalid_input
=====================  ======  ====================  ========================
"""

from __future__ import annotations


class RegGraphError(Exception):
    """Base class. Messages for humans come from the i18n catalogs, not from here."""

    http_status: int = 500
    code: str = "internal_error"
    message_key: str = "error.internal"

    def __init__(self, detail: str = "") -> None:
        # ``detail`` is for logs and tests only. It is never sent to API clients.
        super().__init__(detail or self.code)
        self.detail = detail


class ValidationFailure(RegGraphError, ValueError):
    """Also a ``ValueError`` so pydantic validators can raise it directly."""

    http_status = 422
    code = "invalid_input"
    message_key = "error.invalid_input"


class AuthenticationError(RegGraphError):
    http_status = 401
    code = "unauthenticated"
    message_key = "error.unauthenticated"


class ForbiddenError(RegGraphError):
    http_status = 403
    code = "forbidden"
    message_key = "error.forbidden"


class NotFoundError(RegGraphError):
    http_status = 404
    code = "not_found"
    message_key = "error.not_found"


class ConflictError(RegGraphError):
    http_status = 409
    code = "conflict"
    message_key = "error.conflict"


class RateLimitedError(RegGraphError):
    http_status = 429
    code = "rate_limited"
    message_key = "error.rate_limited"


class RateUnavailableError(RegGraphError):
    """No usable exchange rate (missing or too old)."""

    http_status = 422
    code = "rate_unavailable"
    message_key = "error.invalid_input"
