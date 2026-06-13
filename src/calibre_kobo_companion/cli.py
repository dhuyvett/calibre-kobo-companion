from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_settings
from .db import initialize_companion_db
from .server import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calibre-kobo-companion")
    subcommands = parser.add_subparsers(dest="command")

    subcommands.add_parser("serve", help="Run the HTTP service")
    subcommands.add_parser("init-db", help="Initialize the companion database")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "serve"

    try:
        settings = load_settings()
        initialize_companion_db(settings.companion_db_path)
        if command == "init-db":
            print(f"Initialized companion database at {settings.companion_db_path}")
            return 0
        if command == "serve":
            serve(settings)
            return 0
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    parser.error(f"unknown command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
