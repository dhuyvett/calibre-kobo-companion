from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from calibre_kobo_companion.cli import main


class CliTests(TestCase):
    def test_token_create_list_and_revoke(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "companion.db"
            env = {
                "CALIBRE_LIBRARY_PATH": str(Path(directory) / "library"),
                "COMPANION_DB_PATH": str(db_path),
                "PUBLIC_BASE_URL": "http://example.test",
            }

            create_status, create_stdout, create_stderr = _run_cli(
                ["token", "create", "Clara BW"],
                env,
            )
            token = create_stdout.splitlines()[0]

            list_status, list_stdout, list_stderr = _run_cli(["token", "list"], env)
            revoke_status, revoke_stdout, revoke_stderr = _run_cli(
                ["token", "revoke", token],
                env,
            )
            relist_status, relist_stdout, relist_stderr = _run_cli(["token", "list"], env)

        self.assertEqual(create_status, 0)
        self.assertEqual(create_stderr, "")
        self.assertIn(f"http://example.test/kobo/{token}", create_stdout)

        self.assertEqual(list_status, 0)
        self.assertEqual(list_stderr, "")
        self.assertIn(f"{token}\tactive\tClara BW", list_stdout)

        self.assertEqual(revoke_status, 0)
        self.assertEqual(revoke_stdout, "Revoked token\n")
        self.assertEqual(revoke_stderr, "")

        self.assertEqual(relist_status, 0)
        self.assertEqual(relist_stderr, "")
        self.assertIn(f"{token}\trevoked\tClara BW", relist_stdout)


def _run_cli(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with patch.dict(os.environ, env, clear=True):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(argv)
    return status, stdout.getvalue(), stderr.getvalue()
