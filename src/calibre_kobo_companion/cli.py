from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_settings
from .db import (
    create_device_token,
    initialize_companion_db,
    list_device_tokens,
    revoke_device_token,
)
from .server import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calibre-kobo-companion")
    subcommands = parser.add_subparsers(dest="command")

    subcommands.add_parser("serve", help="Run the HTTP service")
    subcommands.add_parser("init-db", help="Initialize the companion database")
    token_parser = subcommands.add_parser("token", help="Manage Kobo device tokens")
    token_subcommands = token_parser.add_subparsers(dest="token_command", required=True)

    token_create = token_subcommands.add_parser("create", help="Create a device token")
    token_create.add_argument("label", nargs="?", help="Optional device label")

    token_subcommands.add_parser("list", help="List device tokens")

    token_revoke = token_subcommands.add_parser("revoke", help="Revoke a device token")
    token_revoke.add_argument("token", help="Token to revoke")

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
        if command == "token":
            if args.token_command == "create":
                device_token = create_device_token(settings.companion_db_path, args.label)
                print(device_token.token)
                if args.label:
                    print(f"Label: {args.label}")
                print(f"Kobo API base: {settings.public_base_url}/kobo/{device_token.token}")
                return 0
            if args.token_command == "list":
                for device_token in list_device_tokens(settings.companion_db_path):
                    status = "revoked" if device_token.revoked_at else "active"
                    label = device_token.label or ""
                    print(f"{device_token.token}\t{status}\t{label}")
                return 0
            if args.token_command == "revoke":
                if revoke_device_token(settings.companion_db_path, args.token):
                    print("Revoked token")
                    return 0
                print("Token not found or already revoked", file=sys.stderr)
                return 1
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
