# Calibre Kobo Companion Project Plan

## Goal

Build a lightweight, headless service that gives Kobo e-readers network access to an existing Calibre library. The service must treat the Calibre library as read-only, support libraries on local disks or network mounts, and avoid background work or memory use when no Kobo device is actively syncing or downloading books.

This project is not a Calibre-Web replacement. It has no browser UI, no upload flow, no metadata editing, and no ability to modify the Calibre library. The only conversion feature is an optional download-time EPUB-to-KEPUB transform for Kobo devices; converted output is served from temporary or service-owned cache storage and is never written back into the Calibre library.

## Reference Findings

Use these adjacent projects as implementation references, not as dependencies to copy wholesale:

- `../calibre`: source of truth for Calibre's on-disk layout and `metadata.db` schema. The important tables are `books`, `data`, `authors`, `comments`, `series`, `publishers`, `languages`, join tables, and `library_id`.
- `../calibre-web`: best reference for Kobo protocol behavior in `cps/kobo.py`, auth-token setup in `cps/kobo_auth.py`, sync-token encoding in `cps/services/SyncToken.py`, and ORM mapping in `cps/db.py`.
- `../calibre-web-automated`: useful hardening references for network-share handling, reconnect locking, Kobo timestamp selection in `kobo_sync_utils.py`, and tests around timestamp and cover cache behavior.

The reference implementations include many features this project should not include: web UI, users, shelves, reading progress writes, archiving, library mutation, library conversion jobs, upload/ingest jobs, metadata refresh, and scheduled maintenance.

## Product Scope

### In Scope

- A small HTTP service exposing the Kobo API endpoints needed by e-readers.
- Read-only access to an existing Calibre library directory containing `metadata.db`.
- Download of existing EPUB/KEPUB files from the Calibre library.
- Optional on-demand EPUB-to-KEPUB conversion when a Kobo requests a downloadable book and no existing KEPUB is available.
- Cover serving from existing `cover.jpg` files in Calibre book directories.
- Kobo initialization resource responses that point the e-reader at this service.
- Token-based device access suitable for editing `.kobo/Kobo/Kobo eReader.conf`.
- Incremental Kobo library sync based on Calibre book timestamps and the Kobo sync-token header.
- Optional local companion SQLite database for service-owned state only.
- Docker image and systemd-friendly binary/script deployment.
- Raspberry Pi class runtime target.

### Out of Scope

- Browser UI.
- Calibre library writes of any kind.
- Uploading, deleting, editing, or converting books in the Calibre library.
- Writing reading position, annotations, shelves, tags, archive status, or metadata back to Calibre.
- Background scanning that continuously walks the library.
- Proxying or mirroring the Kobo store unless a compatibility issue proves it is required.
- Multi-user administration. A simple static token model is enough for v1.

## Architecture

Use a small Python ASGI or WSGI service. Recommended stack:

- `FastAPI` or `Starlette` for low-overhead request routing.
- Python standard `sqlite3` for direct read-only Calibre queries.
- `uvicorn` as the server process.
- `kepubify` as an optional external binary for request-time EPUB-to-KEPUB conversion.
- `Pillow` only if thumbnail resizing is needed; otherwise serve original covers and let Kobo request sizing be mostly advisory.
- Optional `pydantic-settings` or environment parsing; avoid a large config framework.

Python is the pragmatic choice because the references are Python, Calibre's schema is SQLite, and Raspberry Pi packaging is straightforward. Do not import Calibre itself; it is too large for this service and would pull in unnecessary runtime cost.

### Runtime Shape

The service should be request-driven:

- Open SQLite connections lazily per request or via a tiny pool.
- Open Calibre `metadata.db` with SQLite read-only URI mode.
- Avoid periodic library scans by default.
- Cache only cheap, bounded data such as prepared SQL, small token config, and optional cover metadata.
- Stream book downloads from disk instead of loading files into memory.
- Convert EPUB to KEPUB only in response to an authenticated download request, never during sync or background idle time.
- Re-read Calibre data on each sync request so external Calibre changes are visible without a daemon scanner.

## Storage Model

### Calibre Library

Configured by `CALIBRE_LIBRARY_PATH`, pointing at the directory containing `metadata.db`.

Open using a URI equivalent to:

```text
file:/path/to/metadata.db?mode=ro
```

Consider `immutable=1` only as an optional setting for stable local snapshots. Do not make it the default because network mounts and live Calibre updates may need fresh reads.

Required read behavior:

- Never execute `INSERT`, `UPDATE`, `DELETE`, `PRAGMA journal_mode`, or schema mutation against Calibre's database.
- Build file paths from `books.path`, `data.name`, and `data.format`.
- Validate resolved paths remain inside the configured library root.
- Handle missing files and stale metadata gracefully with 404s.
- Never write converted KEPUB files, sidecars, checksums, or temporary files inside the Calibre library tree.

### Companion Database

Use `COMPANION_DB_PATH`, defaulting to a small SQLite file under the config directory. This database is owned by this service and may be writable.

Initial tables:

```sql
CREATE TABLE device_tokens (
  token TEXT PRIMARY KEY,
  label TEXT,
  created_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE sync_devices (
  token TEXT PRIMARY KEY,
  last_seen_at TEXT,
  FOREIGN KEY(token) REFERENCES device_tokens(token)
);
```

Avoid per-book sync state in v1 unless the Kobo protocol requires deletion tracking. The Kobo `x-kobo-synctoken` header can carry incremental timestamps, matching the Calibre-Web approach.

If converted downloads are cached, store them outside the Calibre library under a service-owned directory, for example:

```text
{COMPANION_CACHE_PATH}/kepub/{book_uuid}/{source_mtime}-{source_size}.kepub.epub
```

The cache is disposable. Deleting it must not affect the Calibre library or sync state.

## Kobo API Surface

Base path:

```text
/kobo/{token}
```

The token is a random server-generated secret. Users configure the Kobo device's `api_endpoint` or related Kobo config entry manually, following the Calibre-Web setup pattern.

### Required Endpoints

- `GET /kobo/{token}/v1/initialization`
  - Return Kobo resource URLs pointing back to this service.
  - Set image templates, library sync URL, auth/device URL, and download-capable resources.

- `POST /kobo/{token}/v1/auth/device`
- `POST /kobo/{token}/v1/auth/refresh`
  - Return dummy bearer tokens as Calibre-Web does. The path token is the real authorization mechanism.

- `GET /kobo/{token}/v1/library/sync`
  - Parse `x-kobo-synctoken`.
  - Query books with Kobo-compatible formats: prefer `KEPUB`, otherwise `EPUB`.
  - Return `NewEntitlement` or `ChangedEntitlement` records with `BookEntitlement` and `BookMetadata`.
  - Return at most a configured page size, default `100`, and set `x-kobo-sync: continue` when more records remain.
  - Write a new `x-kobo-synctoken` response header.

- `GET /kobo/{token}/v1/library/{book_uuid}/metadata`
  - Return metadata for one Calibre book UUID.

- `GET /kobo/{token}/download/{book_id}/{format}`
  - Stream the existing ebook file from the Calibre library when the requested format exists.
  - If Kobo requests KEPUB and only EPUB exists, convert the EPUB to KEPUB on demand and stream the converted file.
  - Support `epub` and `kepub` initially.

- `GET /kobo/{token}/{image_id}/{width}/{height}/false/image.jpg`
- `GET /kobo/{token}/{image_id}/{width}/{height}/{quality}/false/image.jpg`
  - Serve the Calibre `cover.jpg`.
  - Accept cache-busting suffixes such as `{uuid}-{mtime}`.

### Compatibility Stubs

Return small success/empty responses for endpoints Kobo devices call but this service does not implement:

- user profile, loyalty, wishlist, analytics, assets
- product discovery and recommendations
- tag/shelf mutation
- delete/archive requests
- reading-state writes

For mutation-like Kobo requests, return success only when harmless for the device. Log at debug level that the request was ignored because the service is read-only.

## Metadata Mapping

Use Calibre-Web's `get_metadata()` and `create_book_entitlement()` behavior as the baseline:

- `books.uuid` maps to Kobo `Id`, `RevisionId`, `CrossRevisionId`, `EntitlementId`, `WorkId`, and default `CoverImageId`.
- `books.title` maps to `Title`.
- `authors` map to `Contributors` and `ContributorRoles`.
- `comments.text` maps to `Description`.
- `publishers.name` maps to `Publisher.Name`.
- `series.name` and `books.series_index` map to Kobo `Series`.
- `languages.lang_code` maps to a Kobo language code, with a safe fallback of `en`.
- `books.pubdate`, `books.timestamp`, and `books.last_modified` map to Kobo UTC timestamps.
- `data.format` and `data.uncompressed_size` map to `DownloadUrls`.
- EPUB-only books may advertise a KEPUB-compatible download URL when on-demand conversion is enabled. The source Calibre format remains EPUB.

Timestamp selection should use the hardened Calibre-Web Automated behavior:

1. Prefer `books.timestamp`.
2. If a shelf-specific `date_added` concept is ever introduced, use the later value.
3. Fall back to `books.last_modified`.
4. Fall back to `datetime.min`.

For v1, since there are no shelves, use `books.timestamp` for created time and `books.last_modified` for changed time.

## Download-Time KEPUB Conversion

The service should support Kobo-optimized KEPUB downloads without altering the Calibre library. Treat "keypub" as user-facing shorthand for Kobo's KEPUB format.

Recommended behavior:

- If the Calibre library already contains `KEPUB`, stream it directly.
- If the library contains only `EPUB` and `ENABLE_KEPUBIFY=true`, run `kepubify` against the source EPUB and write the result to a temporary file or companion cache path.
- Stream the converted KEPUB result to the e-reader.
- If conversion fails, return a clear 5xx response and log the failure without modifying the library.
- If conversion is disabled or `kepubify` is unavailable, fall back to EPUB download.

Cache policy:

- Cache converted output only under `COMPANION_CACHE_PATH`, never inside the Calibre library.
- Key the cache by book UUID, source format, source file size, and source file mtime.
- Use a per-book conversion lock so simultaneous downloads do not run duplicate conversions.
- Make the cache optional and bounded by configuration. A no-cache mode should convert to a temporary file and delete it after streaming.

Implementation notes:

- Prefer the standalone `kepubify` binary over Calibre conversion tools because it is small and focused.
- Run conversion with low priority where supported, for example `nice`, to avoid hurting Raspberry Pi responsiveness.
- Use subprocess timeouts and size checks.
- Do not advertise converted file sizes unless the converted cache file already exists; otherwise use the EPUB source size or omit size if Kobo tolerates it.

## Read-Only Guarantees

Implement explicit guardrails:

- Calibre database module exposes query functions only; no generic execute method to application code.
- SQLite connection uses `mode=ro`.
- Integration test mounts a fixture library read-only and verifies sync/download still work.
- Integration test confirms Calibre `metadata.db` file mtime is unchanged after sync, metadata, cover, and download requests.
- Container examples mount the library as `:ro`.
- Documentation states that the service never updates Calibre metadata or book files.

## Configuration

Environment-first configuration:

```text
CALIBRE_LIBRARY_PATH=/books/Calibre Library
COMPANION_DB_PATH=/config/companion.db
COMPANION_CACHE_PATH=/config/cache
PUBLIC_BASE_URL=http://kobo-companion.local:8080
LISTEN_HOST=0.0.0.0
LISTEN_PORT=8080
KOBO_SYNC_PAGE_SIZE=100
LOG_LEVEL=info
ALLOW_KOBO_MUTATION_ACKS=true
ENABLE_KEPUBIFY=true
KEPUBIFY_PATH=/usr/local/bin/kepubify
KEPUB_CACHE_MAX_MB=1024
KEPUB_CONVERSION_TIMEOUT_SECONDS=120
```

Token management should be CLI-only:

```text
calibre-kobo-companion token create "Clara BW"
calibre-kobo-companion token list
calibre-kobo-companion token revoke <token>
calibre-kobo-companion serve
```

This avoids adding a UI while still making device setup manageable.

## Performance Plan

- Use direct SQL with focused joins rather than a heavy ORM.
- Select only rows needed for the current sync page.
- Add no long-running watcher by default.
- Stream downloads with file iterators.
- Convert to KEPUB only on demand, and cache converted output outside the library when enabled.
- Use bounded cover handling. If resizing is implemented, cache resized covers under the companion config directory with size and mtime in the key.
- Keep dependencies minimal and avoid importing image libraries unless cover resizing is enabled.
- Use `systemd` socket activation as a later enhancement if idle memory becomes important.

Expected idle footprint should be a single small Python process with no worker queue and no scheduler.

## Security Plan

- Treat the path token as a bearer secret.
- Generate at least 128 bits of randomness per token.
- Do not log full tokens.
- Require token on every Kobo endpoint.
- Validate all file paths against the library root before serving.
- Set conservative response headers for downloads.
- Document that the service should normally be exposed only on a trusted LAN or behind TLS/auth at a reverse proxy.

## Testing Plan

### Unit Tests

- Sync-token parse/build compatibility.
- Calibre metadata row to Kobo metadata mapping.
- Timestamp normalization.
- Format selection: `KEPUB` preferred over `EPUB`; EPUB can be advertised as converted KEPUB when conversion is enabled; unsupported formats ignored.
- Cover image ID normalization and cache-busting suffix handling.
- Path resolution rejects traversal and stale database paths.
- KEPUB cache key changes when source EPUB size or mtime changes.
- Conversion command failures never create or update files inside the Calibre library.

### Integration Tests

- Fixture Calibre library with `metadata.db`, EPUB, KEPUB, and covers.
- Full first sync returns entitlements.
- Incremental sync returns only changed books based on token timestamps.
- Pagination sets `x-kobo-sync: continue`.
- Download streams the expected file.
- KEPUB download converts an EPUB-only book without changing Calibre `metadata.db` or source ebook mtime.
- Repeated KEPUB download uses companion cache when enabled.
- Cover endpoint returns image bytes.
- Calibre library mounted/readable as read-only remains unchanged.

### Device Validation

- Manual test on at least one Kobo device by editing `.kobo/Kobo/Kobo eReader.conf`.
- Validate first sync, subsequent sync, cover display, and download/open behavior.
- Validate behavior when the network mount is temporarily unavailable.

## Implementation Phases

### Phase 1: Skeleton

- Create Python package, CLI entry point, config loader, and HTTP server.
- Add companion DB migrations for token storage.
- Add health endpoint that does not touch the Calibre library.
- Add Dockerfile and basic systemd example.

### Phase 2: Read-Only Calibre Access

- Implement SQLite read-only connection handling.
- Implement queries for books, formats, authors, comments, publisher, series, language, and cover paths.
- Add fixture library tests.
- Add path safety checks.

### Phase 3: Kobo Bootstrap and Auth

- Implement token auth middleware.
- Implement initialization resource response.
- Implement dummy auth/device and auth/refresh responses.
- Add CLI command that prints the Kobo config URL for a token.

### Phase 4: Sync and Metadata

- Implement sync-token encoding/decoding.
- Implement first and incremental library sync.
- Implement metadata mapping and download URL generation.
- Add pagination and continuation headers.

### Phase 5: Files, Covers, and KEPUB Downloads

- Implement ebook streaming.
- Implement optional download-time EPUB-to-KEPUB conversion.
- Implement service-owned conversion cache with source mtime/size invalidation.
- Implement cover serving and optional cache-busting IDs.
- Decide whether thumbnail resizing is needed after device testing.

### Phase 6: Compatibility and Hardening

- Add harmless stubs for extra Kobo endpoints observed during device tests.
- Add network-mount failure handling and clear log messages.
- Add read-only regression tests.
- Add resource usage checks on Raspberry Pi.

### Phase 7: Packaging

- Publish minimal Docker image.
- Add sample `docker-compose.yml` with library mounted read-only.
- Add systemd unit example.
- Add setup documentation for Kobo device configuration.

## Key Risks

- Kobo firmware behavior varies by device and version. Keep compatibility stubs easy to add.
- Book deletion is hard without server-side per-device known-book state. V1 can ignore deletion propagation or document that removed Calibre books may remain on devices until manually deleted.
- Network mounts may expose stale SQLite views or transient errors. Use short-lived read-only connections and clear retry behavior.
- Kobo may require KEPUB for the best reading experience. Download-time conversion solves this without modifying the Calibre library, but it can add latency and CPU load on Raspberry Pi hardware.

## Initial Milestone Definition

The first useful release is complete when a Kobo device can:

1. Initialize against the service with a generated token.
2. Sync all EPUB/KEPUB books from a read-only Calibre library.
3. Display metadata and covers.
4. Download and open a selected book, using on-demand KEPUB conversion for EPUB-only sources when enabled.
5. Repeat sync without rescanning or resending unchanged books.

All of this must pass with the Calibre library mounted read-only.
