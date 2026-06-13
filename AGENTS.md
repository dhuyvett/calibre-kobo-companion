# Agent Guide

This file is a quick orientation for coding agents working in this repository.
Keep durable project documentation in human-readable docs and update this file
only when agent-specific navigation or workflow notes change.

## Start Here

- Read [README.md](README.md) for the project purpose, goals, non-goals,
  development commands, and current runtime entry points.
- Read [docs/project-plan.md](docs/project-plan.md) for product scope,
  architecture, Kobo API plans, metadata mapping, storage rules, and rollout
  priorities.
- Treat those documents as the source of truth. Do not duplicate large sections
  from them into `AGENTS.md`.

## Repository Map

- `src/calibre_kobo_companion/config.py`: environment-driven settings and
  validation.
- `src/calibre_kobo_companion/calibre.py`: read-only Calibre metadata access
  and path safety checks.
- `src/calibre_kobo_companion/db.py`: companion database schema and
  initialization. This database is service-owned and may be writable.
- `src/calibre_kobo_companion/server.py`: current HTTP skeleton.
- `src/calibre_kobo_companion/cli.py`: command-line entry point.
- `tests/`: unit tests for configuration, companion database setup, and server
  behavior.

## Core Constraints

- The Calibre library is read-only. Do not add code that writes to
  `metadata.db`, book files, cover files, sidecars, or cache files inside the
  Calibre library tree.
- Service-owned state belongs in paths such as `COMPANION_DB_PATH` and
  `COMPANION_CACHE_PATH`, outside the Calibre library.
- Keep the service lightweight and request-driven. Avoid background scans,
  browser UI features, Calibre library management, or importing Calibre itself.
- Prefer the standard library unless the project plan or a concrete
  implementation need justifies adding a dependency.

## Development Commands

Use the local source tree on `PYTHONPATH`:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
```

Initialize the companion database:

```sh
CALIBRE_LIBRARY_PATH=/path/to/calibre-library \
COMPANION_DB_PATH=./data/companion.db \
PYTHONPATH=src python3 -m calibre_kobo_companion.cli init-db
```

Run the current skeleton service:

```sh
CALIBRE_LIBRARY_PATH=/path/to/calibre-library \
PYTHONPATH=src python3 -m calibre_kobo_companion.cli serve
```

## Change Guidance

- Update `README.md` when user-facing setup, commands, or project guarantees
  change.
- Update [docs/project-plan.md](docs/project-plan.md) when scope,
  architecture, Kobo protocol behavior, or implementation priorities change.
- Add or update focused tests in `tests/` for behavior changes.
- Keep generated files, caches, and local runtime data out of commits unless
  the project explicitly starts tracking them.
