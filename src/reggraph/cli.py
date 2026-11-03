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
    demo = sub.add_parser("demo", help="offline showcase with invented data (loopback only)")
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--port", type=int, default=8000)
    demo.add_argument(
        "--role", default="admin", choices=["admin", "analyst", "compliance", "viewer"]
    )
    demo.add_argument("--open", action="store_true", help="open the page in the default browser")
    tok = sub.add_parser("issue-token", help="mint a short-lived token (not allowed in prod)")
    tok.add_argument("--tenant", required=True)
    tok.add_argument("--user", required=True)
    tok.add_argument("--role", required=True, choices=[r.value for r in Role])
    tok.add_argument("--ttl", type=int, default=900)
    args = parser.parse_args(argv)

    if args.command == "demo":
        from reggraph.demo import run_demo

        return run_demo(args.host, args.port, args.role, args.open)

    settings = Settings.from_env()
    if args.command == "serve":
        if settings.offline and args.host not in {"127.0.0.1", "::1", "localhost"}:
            sys.stderr.write("refused: offline mode only listens on the loopback address\n")
            return 2
        import uvicorn

        from reggraph.api import create_app

        uvicorn.run(
            create_app(settings),
            host=args.host,
            port=args.port,
            server_header=False,
            access_log=not settings.offline,
            log_level="warning" if settings.offline else "info",
        )
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
