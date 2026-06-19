# Getting TLS Certificates with acme.sh

This document is specific to `acme.sh`. The service should accept ordinary
certificate and private-key files, so other certificate tools can be documented
separately.

## Goal

Use `acme.sh` to obtain and renew a certificate, then deploy the resulting
certificate files to a directory that Calibre Kobo Companion can read:

```text
/config/tls/fullchain.pem
/config/tls/privkey.pem
```

Built-in TLS uses those files through configuration like:

```sh
PUBLIC_BASE_URL=https://kobo.example.com:8443
LISTEN_PORT=8443
TLS_CERT_PATH=/config/tls/fullchain.pem
TLS_KEY_PATH=/config/tls/privkey.pem
```

## Important Constraints

- The Kobo must trust the certificate chain.
- Publicly trusted certificates usually require a real domain name.
- For a home network, DNS validation is often easier than exposing an HTTP
  challenge port to the internet.
- `acme.sh` should deploy certificates into a service-owned config directory;
  do not write certificate files into the Calibre library.
- The service should read deployed certificate files, not `acme.sh`'s internal
  account or working files.

## Install acme.sh

Follow the upstream `acme.sh` installation instructions for the target machine.
After installation, confirm the command is available:

```sh
acme.sh --version
```

## Issue a Certificate

Use a validation method supported by your DNS or network setup. For DNS
validation, the shape is:

```sh
acme.sh --issue --dns <dns_provider> -d kobo.example.com
```

`<dns_provider>` and the required environment variables depend on the DNS
provider. Keep those credentials outside this repository and outside the
Calibre library.

For an HTTP challenge, the shape is:

```sh
acme.sh --issue -d kobo.example.com -w /path/to/webroot
```

HTTP validation requires the ACME server to reach the challenge URL from the
public internet.

## Deploy Certificate Files

Create a service-owned TLS directory:

```sh
mkdir -p /config/tls
```

Install the issued certificate and key into that directory:

```sh
acme.sh --install-cert -d kobo.example.com \
  --fullchain-file /config/tls/fullchain.pem \
  --key-file /config/tls/privkey.pem \
  --reloadcmd "systemctl restart calibre-kobo-companion"
```

Adjust the reload command for the deployment. For a foreground development
server, omit `--reloadcmd` and restart the process manually after renewal.

## Configure the Service

Start the service with the deployed certificate paths:

```sh
CALIBRE_LIBRARY_PATH=/path/to/calibre-library \
COMPANION_DB_PATH=/config/companion.db \
PUBLIC_BASE_URL=https://kobo.example.com:8443 \
LISTEN_HOST=0.0.0.0 \
LISTEN_PORT=8443 \
TLS_CERT_PATH=/config/tls/fullchain.pem \
TLS_KEY_PATH=/config/tls/privkey.pem \
PYTHONPATH=src python3 -m calibre_kobo_companion.cli serve
```

Then set the Kobo `api_endpoint` to the HTTPS base URL printed by the token
command:

```ini
[OneStoreServices]
api_endpoint=https://kobo.example.com:8443/kobo/<token>
```

## Renewal Notes

`acme.sh` installs its own renewal job during normal setup. The important part
for this service is the `--install-cert` deploy step: renewal should update the
deployed `fullchain.pem` and `privkey.pem` files and then restart or reload the
service so the new certificate is used.

## Troubleshooting

- If the Kobo cannot connect over HTTPS, verify the certificate hostname
  matches `PUBLIC_BASE_URL` and `api_endpoint`.
- If desktop `curl` works but the Kobo fails, the Kobo may not trust the
  certificate chain or may not support the selected TLS configuration.
- If renewal succeeds but the service still presents the old certificate,
  confirm the `--reloadcmd` runs successfully.
