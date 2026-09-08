# Security

intent-ledger does not yet implement an authentication or authorization layer. Every route is reachable by anyone who can reach the server.

**Do not expose a running instance directly to the internet.** Recommended
deployment steps to keep in mind assuming no auth:

- Run it only on `localhost` or a private/VPN-only network.
- If you need remote access, put it behind your own authenticating reverse proxy rather than relying on anything in this app.
- Always run behind TLS if reachable over any network you don't fully control.

<br>

## Third-party requests

The app makes no third party requests and works locally. Chart.js and both webfonts are plain files committed to the repo and served from your own host, so loading a page does not tell a CDN or a font service that you are looking at your finances.

<br>

## Your data

- The SQLite database (`DATA_DIR`, default `./data/`) contains your real financial transactions. It is not encrypted at rest by this app. Use your host's disk encryption if that matters to you.
- Never commit `data/` or your `.env` file. Both are gitignored by default; don't override that.
- `fixtures/sample_statements/` contains only synthetic data. They are safe to import and safe to delete.

<br>

## Reporting a vulnerability

For anything that could expose data on a shared or exposed deployment, please use GitHub's private vulnerability reporting (Security tab -> Report a vulnerability) rather than filing a public issue. For anything else, a public issue is fine.
