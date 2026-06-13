# Calibre Kobo Companion

Calibre Kobo Companion is a lightweight, headless service for giving Kobo e-readers network access to an existing Calibre library.

The project is designed for a simple home setup: point it at a Calibre library, run it on a small machine such as a Raspberry Pi, configure the Kobo device to use the service endpoint, and sync/download books over the local network.

## Goals

- Provide Kobo e-reader sync and download access to books already present in a Calibre library.
- Treat the Calibre library as strictly read-only, including libraries stored on network mounts.
- Avoid browser UI, background library management, and long-running work when no device is actively using the service.
- Keep runtime dependencies and resource usage small enough for Raspberry Pi class hardware.
- Serve existing EPUB and KEPUB files from the library.
- Optionally convert EPUB files to Kobo KEPUB format at download time, without modifying the Calibre library.

## Non-Goals

This is not Calibre-Web and is not a Calibre library manager. It will not provide:

- A web UI.
- Book uploads.
- Metadata editing.
- Book deletion.
- Calibre library conversion jobs.
- Calibre database writes.
- Reading progress, annotation, shelf, tag, or archive writes back to Calibre.

Any local state required by the service, such as device tokens or optional converted-download cache files, belongs in service-owned storage outside the Calibre library.

## Read-Only Library Promise

The Calibre library is the source of truth. This service should only read:

- `metadata.db`
- existing book files
- existing cover files

Download-time KEPUB conversion, when enabled, writes only to temporary storage or a companion cache directory. Converted files are never added to Calibre metadata and are never written into the Calibre library tree.

## Project Plan

The current implementation plan is in [docs/project-plan.md](docs/project-plan.md).

## Development

Run the current test suite without installing extra dependencies:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
```

Initialize the companion database:

```sh
CALIBRE_LIBRARY_PATH=/path/to/calibre-library \
COMPANION_DB_PATH=./data/companion.db \
PYTHONPATH=src python3 -m calibre_kobo_companion.cli init-db
```

Start the skeleton service:

```sh
CALIBRE_LIBRARY_PATH=/path/to/calibre-library \
PYTHONPATH=src python3 -m calibre_kobo_companion.cli serve
```

The initial service exposes `GET /health`. Read-only Calibre metadata access is underway; Kobo endpoints are planned but not implemented yet.
