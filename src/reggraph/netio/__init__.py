"""Outbound network access, sandboxed (Phase 5). The only module that opens sockets."""

from reggraph.netio.fetch import (
    Fetcher,
    FetchResult,
    SafeFetcher,
    UpstreamError,
    https_transport,
    validate_url,
)

__all__ = [
    "FetchResult",
    "Fetcher",
    "SafeFetcher",
    "UpstreamError",
    "https_transport",
    "validate_url",
]
