# intent-ledger

intent-ledger is a self-hosted, double-entry personal finance app. You import a bank statement and it builds the ledger from it. Rules and payee memory handle categorization, while a Hungarian-algorithm solver matches transfers between your own accounts. Budgets track spending by category. Subscriptions and cost-center projects can also be followed over time.

The usual accounting reports are there too: balance sheets, cashflows, and other reports. They can be exported as formatted XLSX or CSV files. The whole thing is one small Flask app. It runs locally on your machine and you have control of your data.

<br>

## Status

- Self-hosted-only. There is **no authentication layer yet**, see [docs/SECURITY.md](docs/SECURITY.md) before deploying anywhere but `localhost` or a private network.
- A change log needs to be added for history and restoration.
- More accent colors and themes.
- A minimal read-only JSON API is under consideration for scripting against your own data (balances, transactions, budget status).
- Docker image

<br>

## Features

- Double-entry ledger compiled from imported bank statements and manual entries
- Bank-agnostic import via a [canonical CSV/XLS/XLSX template](docs/canonical-template.md), with a pluggable parser interface for bank-specific formats
  ([docs/adding-a-parser.md](docs/adding-a-parser.md))
- Resolution Ladder:
  - Rules/Transfers first,
  - Overrides second,
  - Merchant-alias-based auto-categorization third,   
with a triage inbox for anything unmatched
- Hungarian algorithm to detect transfers between accounts in the uploaded bank statements.
- Envelope budgeting, expense reports, recurring-payment/subscription tracking,
  and cost-center projects
- Formatted XLSX/CSV exports: per-account statements (balance sheet, cashflow, categorized transactions), per-project reports, and yearly reports

<br>

## Quickstart

Requires [uv](https://docs.astral.sh/uv/). 

```sh
uv sync

cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"   # put this in .env as SECRET_KEY

uv run intent-ledger run
```

Open `http://127.0.0.1:5000`.

intent-ledger serves everything from the host itself. There are no third party connections or services used.

### Try it with sample data

Import the sample statements under `fixtures/` via the Import page, one file per account (Checking, Savings, Credit Card in canonical CSV format).

### Production

Run behind a reverse proxy with TLS using [gunicorn](https://gunicorn.org)
and the `wsgi.py` entrypoint:

```sh
uv run gunicorn -b 127.0.0.1:8000 wsgi:app
```

<br>

## Importing statements

1. Create an account under Mappings.
2. Go to Import, pick the account and a format (the canonical CSV/XLS/XLSX
   template by default), and upload one or more files.
3. Click "Import & rebuild ledger" (also available from the sidebar) to
   ingest pending uploads and recompile the ledger.

See [docs/canonical-template.md](docs/canonical-template.md) for the exact
column spec, and [docs/adding-a-parser.md](docs/adding-a-parser.md) if you
want to add a parser for your bank's raw export format instead.

<br>

## CLI

`intent-ledger` is a shorter entrypoint for the same commands as `flask --app intent_ledger <command>`:

```
$ intent-ledger --help
Usage: intent-ledger [OPTIONS] COMMAND [ARGS]...

  intent-ledger command-line interface.

Options:
  --version             Show the intent-ledger version and exit.
  -A, --app IMPORT      Flask app to load.
  --debug / --no-debug  Run with the debugger/reloader enabled.
  --help                Show this message and exit.

Commands:
  export-all      Export account statements, project reports, and yearly...
  import          Import every pending uploaded statement and rebuild the...
  rebuild-ledger  Recompute the ledger and subscription matches from...
  routes          Show the routes for the app.
  run             Run a development server.
  shell           Run a shell in the app context.
```

This can be done from the UI as well but is included for convenience:
`export-all` writes the following into `DATA_DIR/finance/exports/`:
- a statement per account,
- a report per project,
- a yearly report for every year with transaction data.

Use `--no-accounts`, `--no-projects`, or `--no-yearly` to skip a category;
see `intent-ledger export-all --help`.

<br>

## Developing

```sh
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run ruff format .
```

Front-end dependencies are committed files:

| Dependency | Purpose |
|---|---|
| Tailwind CSS | Compiles `input.css` into `app.css` |
| Chart.js | Charts on Budget, Expenses, and Projects |
| Inter | UI font |
| IBM Plex Mono | Numbers and code |

Edit `static/css/input.css` to change styles. `app.css` is generated via Tailwind CSS [Standalone CLI](https://tailwindcss.com/blog/standalone-cli). These files are updated in every release.

<br>

## Security

See [docs/SECURITY.md](docs/SECURITY.md) for deployment, what's done with your data, and how to report a vulnerability.

<br>

## Contributing

New features, bug reports, and small fixes are welcome. See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for how changes are made and reviewed.

<br>

## License

This app is distributed under the [MIT](LICENSE) License. I reserve the right to place future versions of this project under a different license.

