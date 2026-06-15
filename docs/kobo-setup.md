# Running the Server and Connecting a Kobo

This guide covers a local development or home-LAN setup. The service is still
in progress: Kobo initialization, auth, library sync, and book metadata are
implemented. Existing EPUB/KEPUB downloads and cover serving are also
implemented, along with minimal compatibility stubs for common Kobo
user/assets/analytics requests. Optional KEPUB conversion through `kepubify`
is implemented. Full compatibility coverage, TLS, and packaging are not
implemented yet.

## Requirements

- Python 3.
- A Calibre library directory containing `metadata.db`.
- The machine running this service must be reachable from the Kobo over the
  same network.
- The Calibre library should be mounted read-only when possible.

## Choose Paths and URL

Pick three values before starting:

```sh
CALIBRE_LIBRARY_PATH=/path/to/calibre-library
COMPANION_DB_PATH=./data/companion.db
PUBLIC_BASE_URL=http://192.168.1.50:8080
```

Use the actual LAN IP address or hostname of the machine running the service
for `PUBLIC_BASE_URL`. Do not use `localhost` for a real Kobo device because
that would point the Kobo back at itself.

`COMPANION_DB_PATH` is service-owned writable state. Keep it outside the
Calibre library.

To advertise and serve KEPUB downloads for EPUB-only books, also configure:

```sh
ENABLE_KEPUBIFY=true
KEPUBIFY_PATH=/usr/local/bin/kepubify
COMPANION_CACHE_PATH=./data/cache
```

Converted files are cached under `COMPANION_CACHE_PATH`, outside the Calibre
library. If conversion is disabled or not configured, EPUB-only books are
advertised and served as EPUB.

For HTTPS, use an `https://` `PUBLIC_BASE_URL` and configure the server with
certificate and key files once built-in TLS support is implemented. The
certificate can come from any method trusted by the Kobo. If using `acme.sh`,
keep those details in [acme-sh-certificates.md](acme-sh-certificates.md).

## Initialize the Companion Database

From the repository root:

```sh
CALIBRE_LIBRARY_PATH=/path/to/calibre-library \
COMPANION_DB_PATH=./data/companion.db \
PUBLIC_BASE_URL=http://192.168.1.50:8080 \
PYTHONPATH=src python3 -m calibre_kobo_companion.cli init-db
```

## Create a Kobo Device Token

```sh
CALIBRE_LIBRARY_PATH=/path/to/calibre-library \
COMPANION_DB_PATH=./data/companion.db \
PUBLIC_BASE_URL=http://192.168.1.50:8080 \
PYTHONPATH=src python3 -m calibre_kobo_companion.cli token create "Clara BW"
```

The command prints the token and the Kobo API base URL:

```text
<token>
Label: Clara BW
Kobo API base: http://192.168.1.50:8080/kobo/<token>
```

Treat the token as a bearer secret. Anyone who can reach the service and knows
the token can use the Kobo endpoints for that device.

## Start the Server

```sh
CALIBRE_LIBRARY_PATH=/path/to/calibre-library \
COMPANION_DB_PATH=./data/companion.db \
PUBLIC_BASE_URL=http://192.168.1.50:8080 \
LISTEN_HOST=0.0.0.0 \
LISTEN_PORT=8080 \
PYTHONPATH=src python3 -m calibre_kobo_companion.cli serve
```

Check the health endpoint from another machine on the same network:

```sh
curl http://192.168.1.50:8080/health
```

Expected response:

```json
{"service": "calibre-kobo-companion", "status": "ok"}
```

## Configure the Kobo

1. Connect the Kobo to a computer over USB.
2. Open the mounted Kobo storage.
3. Find this file:

   ```text
   .kobo/Kobo/Kobo eReader.conf
   ```

4. Make a backup copy of the file.
5. Find or add the `[OneStoreServices]` section.
6. Set `api_endpoint` to the Kobo API base URL printed by the token command:

   ```ini
   [OneStoreServices]
   api_endpoint=http://192.168.1.50:8080/kobo/<token>
   ```

7. Save the file and safely eject the Kobo.
8. Disconnect the Kobo and run a sync from the device.

## What Should Work Now

With the current implementation, the Kobo should be able to:

- Request initialization resources from this service.
- Authenticate through the dummy Kobo auth endpoints.
- Request library sync.
- Receive book metadata for EPUB and KEPUB books in the Calibre library.
- Download existing EPUB and KEPUB files from the Calibre library.
- Download converted KEPUB files for EPUB-only books when `kepubify` is
  enabled.
- Request cover images from Calibre `cover.jpg` files.
- Receive harmless empty responses for common user, assets, and analytics
  requests made by Kobo firmware during sync.

EPUB-only books are advertised and served as EPUB unless KEPUB conversion is
enabled.

## Token Management

List tokens:

```sh
CALIBRE_LIBRARY_PATH=/path/to/calibre-library \
COMPANION_DB_PATH=./data/companion.db \
PYTHONPATH=src python3 -m calibre_kobo_companion.cli token list
```

Revoke a token:

```sh
CALIBRE_LIBRARY_PATH=/path/to/calibre-library \
COMPANION_DB_PATH=./data/companion.db \
PYTHONPATH=src python3 -m calibre_kobo_companion.cli token revoke <token>
```

After revoking a token, a Kobo configured with that token will receive
unauthorized responses from this service.

## Troubleshooting

- If `/health` works locally but not from another device, check firewall rules,
  the server IP address, `LISTEN_HOST`, and Wi-Fi network isolation.
- If the Kobo gets unauthorized responses, create a new token or confirm that
  the token in `api_endpoint` exactly matches an active token.
- If sync returns no books, confirm the Calibre library contains EPUB or KEPUB
  formats and that `CALIBRE_LIBRARY_PATH` points at the directory containing
  `metadata.db`.
- If the Kobo uses HTTPS-only behavior on a particular firmware version, use
  built-in TLS once implemented or put the service behind a trusted reverse
  proxy, then set `PUBLIC_BASE_URL` plus `api_endpoint` to the HTTPS URL.
