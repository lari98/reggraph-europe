"""Secure connectors (Phase 5): ECB reference rates and an EUR-Lex source watch."""

from reggraph.connectors.ecb import parse_ecb_rates
from reggraph.connectors.eurlex import FeedItem, parse_feed
from reggraph.connectors.service import AlertRecord, ConnectorService, RunRecord

__all__ = [
    "AlertRecord",
    "ConnectorService",
    "FeedItem",
    "RunRecord",
    "parse_ecb_rates",
    "parse_feed",
]
