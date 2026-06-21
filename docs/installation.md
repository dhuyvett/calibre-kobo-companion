# Installation from GitHub

This guide installs Calibre Kobo Companion from a GitHub checkout. It assumes a
Linux host such as a Raspberry Pi, Python 3.11 or newer, and an existing Calibre
library mounted read-only or otherwise treated as read-only.

Repository URL:

```text
https://github.com/dhuyvett/calibre-kobo-companion
```

## Install Runtime Files

Create a service user and install the source tree:

```sh
sudo useradd --system --home /opt/calibre-kobo-companion --shell /usr/sbin/nologin calibre-kobo
sudo mkdir -p /opt/calibre-kobo-companion /var/lib/calibre-kobo-companion /etc/calibre-kobo-companion/tls
sudo chown calibre-kobo:calibre-kobo /opt/calibre-kobo-companion /var/lib/calibre-kobo-companion

sudo -u calibre-kobo git clone https://github.com/dhuyvett/calibre-kobo-companion.git /opt/calibre-kobo-companion/app
sudo -u calibre-kobo python3 -m venv /opt/calibre-kobo-companion/venv
sudo -u calibre-kobo /opt/calibre-kobo-companion/venv/bin/pip install -e /opt/calibre-kobo-companion/app
```

The package has no required Python dependencies beyond the standard library.

## Configure Environment

Create an environment file:

```sh
sudo install -o root -g calibre-kobo -m 0640 /dev/null /etc/calibre-kobo-companion.env
sudoedit /etc/calibre-kobo-companion.env
```

Example:

```sh
CALIBRE_LIBRARY_PATH=/mnt/calibre-library
COMPANION_DB_PATH=/var/lib/calibre-kobo-companion/companion.db
COMPANION_CACHE_PATH=/var/lib/calibre-kobo-companion/cache
PUBLIC_BASE_URL=https://kobo.example.com:8443
LISTEN_HOST=0.0.0.0
LISTEN_PORT=8443
KOBO_SYNC_MODE=hybrid
KOBO_PROXY_TIMEOUT_SECONDS=20
ENABLE_KEPUBIFY=false
KEPUB_CACHE_MAX_MB=1024
TLS_CERT_PATH=/etc/calibre-kobo-companion/tls/fullchain.pem
TLS_KEY_PATH=/etc/calibre-kobo-companion/tls/privkey.pem
LOG_LEVEL=info
```

If TLS is enabled, the `calibre-kobo` service user must be able to read the
certificate and private key, and traverse their parent directories. A typical
permission setup is:

```sh
sudo chown root:calibre-kobo /etc/calibre-kobo-companion/tls/fullchain.pem
sudo chown root:calibre-kobo /etc/calibre-kobo-companion/tls/privkey.pem
sudo chmod 0644 /etc/calibre-kobo-companion/tls/fullchain.pem
sudo chmod 0640 /etc/calibre-kobo-companion/tls/privkey.pem
sudo chmod 0750 /etc/calibre-kobo-companion /etc/calibre-kobo-companion/tls
```

If the certificate files are symlinks, every directory in the symlink target
path must also be traversable by `calibre-kobo`. Prefer deploying real
certificate files into `/etc/calibre-kobo-companion/tls`; with `acme.sh`, use
`--install-cert` to write deployed files there instead of pointing the service
at `acme.sh`'s internal account files.

Use `KOBO_SYNC_MODE=local` if you only want Calibre books. Use
`KOBO_SYNC_MODE=hybrid` if Kobo Store or OverDrive/Libby content should keep
syncing through Kobo.

If you use KEPUB conversion, install `kepubify` separately from
<https://pgaskin.net/kepubify/> and set:

```sh
ENABLE_KEPUBIFY=true
KEPUBIFY_PATH=/usr/local/bin/kepubify
```

Converted files are stored under `COMPANION_CACHE_PATH` and pruned according to
`KEPUB_CACHE_MAX_MB`.

## Initialize and Create a Token

```sh
sudo -u calibre-kobo /bin/bash -c \
  'set -a; . /etc/calibre-kobo-companion.env; set +a; exec /opt/calibre-kobo-companion/venv/bin/calibre-kobo-companion init-db'

sudo -u calibre-kobo /bin/bash -c \
  'set -a; . /etc/calibre-kobo-companion.env; set +a; exec /opt/calibre-kobo-companion/venv/bin/calibre-kobo-companion token create "Kobo"'
```

The token command prints the Kobo API base URL. Use that URL for the Kobo
`api_endpoint` setting described in [kobo-setup.md](kobo-setup.md).

## Raspberry Pi systemd Service

Create `/etc/systemd/system/calibre-kobo-companion.service`:

```ini
[Unit]
Description=Calibre Kobo Companion
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=calibre-kobo
Group=calibre-kobo
EnvironmentFile=/etc/calibre-kobo-companion.env
WorkingDirectory=/opt/calibre-kobo-companion/app
ExecStart=/opt/calibre-kobo-companion/venv/bin/calibre-kobo-companion serve
Restart=on-failure
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/calibre-kobo-companion
ReadOnlyPaths=/mnt/calibre-library /etc/calibre-kobo-companion

[Install]
WantedBy=multi-user.target
```

Adjust `ReadOnlyPaths` if your Calibre library or TLS files live elsewhere.
If the Calibre library is mounted from the network, make sure the mount is
available before the service starts.

Enable and start the service:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now calibre-kobo-companion
sudo systemctl status calibre-kobo-companion
```

View logs:

```sh
journalctl -u calibre-kobo-companion -f
```

Check the health endpoint from another machine:

```sh
curl https://kobo.example.com:8443/health
```

## Updating from GitHub

```sh
sudo systemctl stop calibre-kobo-companion
sudo -u calibre-kobo git -C /opt/calibre-kobo-companion/app pull --ff-only
sudo -u calibre-kobo /opt/calibre-kobo-companion/venv/bin/pip install -e /opt/calibre-kobo-companion/app
sudo systemctl start calibre-kobo-companion
```

Run tests after updating if this is a development install:

```sh
cd /opt/calibre-kobo-companion/app
sudo -u calibre-kobo PYTHONPATH=src python3 -m unittest discover -s tests
```
