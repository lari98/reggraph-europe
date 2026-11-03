"""Secret-safe logging (Phase 5 exit gate: *no secrets in logs*).

Every log record is rewritten **when it is created**, before any handler, formatter or test
capture can see it. That makes the protection independent of how logging is configured.

=====  ===========================================================================
L1     ``Authorization: Bearer ...`` values are masked.
L2     RegGraph tokens (``v1.<kid>.<payload>.<sig>``) and JWTs (``eyJ...``) are masked.
L3     ``password=``, ``secret=``, ``token=``, ``api_key=``, ``dsn=`` ... values are masked.
L4     Passwords inside URLs (``scheme://user:password@host``) are masked.
L5     Exception tracebacks are rendered once and masked the same way.
L6     Over-long messages are cut before any pattern runs (no regex blow-up on huge input).
=====  ===========================================================================

Masking is best effort on top of the first rule of the project: secrets are never put into
log calls in the first place (``Settings`` hides keys and DSNs from ``repr``).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

MASK: Final = "[redacted]"
MAX_MESSAGE_CHARS: Final = 8000

_BEARER: Final = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}")
_REGGRAPH_TOKEN: Final = re.compile(
    r"\bv1\.[A-Za-z0-9_-]{1,16}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}"
)
_JWT: Final = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_URL_PASSWORD: Final = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+):[^@\s/]+@")
_KEYWORD: Final = re.compile(
    r"(?i)\b([\w-]{0,30}(?:password|passwd|secret|token|api[_-]?key|authorization|credential|dsn)"
    r"[\w-]{0,10}[\"']?)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)


def redact(text: str) -> str:
    """Return ``text`` with credentials masked."""
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS] + "...[truncated]"
    text = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", text)
    text = _REGGRAPH_TOKEN.sub(MASK, text)
    text = _JWT.sub(MASK, text)
    text = _URL_PASSWORD.sub(lambda m: f"{m.group(1)}:{MASK}@", text)
    return _KEYWORD.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)


def _sanitise(record: logging.LogRecord) -> None:
    try:
        message = record.getMessage()
    except Exception:
        message = str(record.msg)
    record.msg = redact(message)
    record.args = None
    if record.exc_info and record.exc_info[0] is not None and not record.exc_text:
        record.exc_text = logging.Formatter().formatException(record.exc_info)
    if record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)


_INSTALLED_FLAG: Final = "_reggraph_redacting"


def install_log_redaction() -> None:
    """Wrap the log-record factory once. Safe to call many times."""
    current = logging.getLogRecordFactory()
    if getattr(current, _INSTALLED_FLAG, False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        _sanitise(record)
        return record

    setattr(factory, _INSTALLED_FLAG, True)
    logging.setLogRecordFactory(factory)
