"""Outbound network access, sandboxed (Phase 5). The only module that opens sockets."""

from reggraph.netio.fetch import (
    Fetcher,
    FetchResult,
    OfflineFetcher,
    SafeFetcher,
    UpstreamError,
    https_transport,
    validate_url,
)

__all__ = [
    "FetchResult",
    "Fetcher",
    "OfflineFetcher",
    "SafeFetcher",
    "UpstreamError",
    "https_transport",
    "validate_url",
]
