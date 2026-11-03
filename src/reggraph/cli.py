"""Command line: ``reggraph serve`` and ``reggraph issue-token`` (development only)."""

from __future__ import annotations

import argparse
import sys

from reggraph.config import Settings
from reggraph.core.clock import SystemClock
from reggraph.security.rbac import Principal, Role
from reggraph.security.tokens import TokenSigner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reggraph")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    tok = sub.add_parser("issue-token", help="mint a short-lived token (not allowed in prod)")
    tok.add_argument("--tenant", required=True)
    tok.add_argument("--user", required=True)
    tok.add_argument("--role", required=True, choices=[r.value for r in Role])
    tok.add_argument("--ttl", type=int, default=900)
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.command == "serve":
        import uvicorn

        from reggraph.api import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port, server_header=False)
        return 0

    if settings.env == "prod":
        sys.stderr.write("refused: tokens come from your identity provider in prod\n")
        return 2
    signer = TokenSigner(settings.token_keys, settings.active_kid, SystemClock())
    principal = Principal(args.user, args.tenant, frozenset({Role(args.role)}))
    sys.stdout.write(signer.issue(principal, ttl_seconds=args.ttl) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
