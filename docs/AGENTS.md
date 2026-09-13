# AGENTS.md

This guide is for AI coding agents (Claude Code, Copilot, Codex, etc.) working in this repo.
See [docs/CONTRIBUTING.md](CONTRIBUTING.md) for the human version. See [.github/agents/rules.md](../.github/agents/rules.md) for what contributors must set up to make the rules below actually be followed.

<br>

## Project overview

**intent-ledger** is a self-hosted, double-entry personal finance app. You import a bank statement and it builds the ledger from it: rules and payee memory handle categorization, a Hungarian-algorithm solver matches transfers between your own accounts, and budgets track spending by category.

- **License**: MIT
- **Primary language**: Python 3.12+
- **Framework**: Flask, server-rendered with Jinja templates, no SPA/JS framework
- **Storage**: SQLite (single file, WAL mode), no external services
- **Build/deps**: [uv](https://docs.astral.sh/uv/)

<br>

## Quick start commands

```bash
# Install dependencies (including dev extras)
uv sync --all-extras

# Run the test suite
uv run pytest

# Lint (ALWAYS run before calling a change done)
uv run ruff check .

# Format
uv run ruff format .

# Run the dev server
uv run intent-ledger run
```

CI (`.github/workflows/ci.yml`) runs `pytest`, `ruff check .`, and `ruff format --check .` as a single required gate on every PR.
Run all three locally before finishing a change; don't rely on CI to catch formatting.

<br>

## Architecture & package structure

Everything lives under `src/intent_ledger/`.

- **`accounting/`**: business logic. One module per domain concept (`ledger.py`, `budget.py`, `accounts.py`, `rules.py`, `subscriptions.py`, `projects.py`, `mappings.py`, `payees.py`, `manual.py`, `counterparties.py`, `resolution.py`).
  Each public function here is what a route calls; most modules also have their own module-level private helpers, not classes.
- **`accounting/repositories/`**: thin classes wrapping raw SQL per table (`AccountRepository`, `SplitRepository`, etc.). CRUD only: list/get, create, update, delete. No business logic here.
- **`routes/`**: Flask blueprints. Kept thin: parse the request, call into `accounting/*`, render a template or redirect. Business logic does not belong in a route handler.
- **`importer/`**: turns an uploaded bank statement into rows in `transactions`. `parsers/` holds one parser per input format (`canonical.py` is the built-in CSV/XLS/XLSX template; `base.py` defines the parser interface for bank-specific formats).
- **`analytics/`**: reporting and exports: `reports.py`, `charts.py`, `workbook.py` (XLSX/CSV generation).
- **`domain/`**: shared value types. `money.py` defines `Money`, an integer-cents type; **never use a `float` for a currency amount**, always go through `Money`. `models.py` holds other small shared types.
- **`templates/`** + **`static/`**: Jinja templates and committed front-end assets (Tailwind-compiled CSS, vendored JS, fonts). See [Front-end](#front-end) below.
- **`db.py`**: the `Database` wrapper (`db = Database()` singleton). `db.transaction()` is a context manager yielding a `sqlite3.Connection` with dict-row results; it commits on success and rolls back on exception. Most business-logic functions take a `conn` and are called from inside a `with db.transaction() as conn:` block one level up.
- **`schema.sql`**: the entire DB schema, applied with `executescript` on `db.initialize()`. Every `CREATE TABLE` uses `IF NOT EXISTS`, so schema.sql only affects *new* databases; there is no migration runner yet. Changing an existing local database's schema is a manual, one-off operation. Keep this in mind before editing `schema.sql` for an already-deployed feature.
- **`service.py`**: top-level orchestration (`import_pending_statements`, `rebuild_ledger_and_subscriptions`, `export_all`) that `cli.py` and the routes call.
- **`cli.py`**: the `intent-ledger` CLI entrypoint (a Click `FlaskGroup`), see `intent-ledger --help` for commands.

<br>

## Development workflow

1. Read the relevant `accounting/*.py` and `accounting/repositories/*.py` files before changing behavior.
   Routes are a thin wrapper; the real logic is in `accounting/`.
2. Make focused, incremental changes.
   Don't refactor unrelated code in the same change.
3. If you touched `intent_ledger/importer/` or `intent_ledger/accounting/ledger.py`, add or update a test. This is not optional; see [Correctness](#correctness).
4. Run `uv run pytest`, `uv run ruff check .`, and `uv run ruff format .` before calling the change done.

### Correctness

`intent_ledger/importer/` and `intent_ledger/accounting/ledger.py` compute people's actual money.
Bugs there are the highest-severity class of issue in this repo.
Read the resolution ladder in [intent_ledger/accounting/resolution.py](../src/intent_ledger/accounting/resolution.py) before touching how a transaction's account is decided.

<br>

## Code style & conventions

- **Money**: always `intent_ledger.domain.money.Money` (integer cents). Never a bare `float` or `int` of dollars for a currency amount.
- **Code organization**: keep each public function followed immediately by the private helpers it calls, in call order, so a file reads top-to-bottom like its call flow. Don't scatter helpers elsewhere in the file or bury them ahead of their caller. Bookkeeping that's secondary to a function's main purpose (e.g. snapshotting state just to compute a return value) goes below the main flow, not mixed into it. See `accounting/ledger.py` and `accounting/resolution.py` for the pattern.
- **Repositories**: classes under `accounting/repositories/` follow the ordinary CRUD convention (list/get, then create, then update, then delete). Leave that structure as is rather than applying the rule above to them.
- **Comments**: default to none. Only add one when the *why* is non-obvious: a hidden constraint, a workaround, or a subtle invariant. Don't add one that just restates what the code already says.
- **SQL**: raw `sqlite3`, no ORM. Queries go through `conn.execute(...)` inside a `db.transaction()` block; rows come back as dicts via `dict_factory`.

### Front-end

Front-end dependencies are committed files, not a package manager:

| Dependency | Purpose |
|---|---|
| Tailwind CSS | Compiles `static/css/input.css` into `static/css/app.css` |
| Chart.js | Charts on Budget, Expenses, and Projects |
| Inter | UI font |
| IBM Plex Mono | Numbers and code |

**Never add a CDN `<script>` or `<link>`.** Vendor the file under `static/` and reference it locally instead.
Edit `static/css/input.css` to change styles; `app.css` is generated via the Tailwind CSS [Standalone CLI](https://tailwindcss.com/blog/standalone-cli), not npm.

<br>

## Testing patterns

- `pytest` + `pytest-flask`, configured via `[tool.pytest.ini_options]` in `pyproject.toml` (`testpaths = ["tests"]`).
- `tests/conftest.py` gives every test a fresh SQLite database in a temp path (the `conn` fixture) plus factory fixtures (`account_factory`, `transaction_factory`, `transfer_rule_factory`) for building up test data without hand-writing inserts.
- Test files roughly mirror the modules they cover (`test_ledger.py`, `test_budget.py`, `test_rules_overrides.py`, `test_importer.py`, ...). Put a new test next to its closest existing sibling rather than starting a new file for a small addition.
- Run a single test file with `uv run pytest tests/test_ledger.py`, or a single test with `-k`.

<br>

## Important directories & files

- `pyproject.toml`: dependencies, `ruff` config (line length 110, target py312, `docstring-code-format` on), pytest config
- `src/intent_ledger/schema.sql`: full DB schema, see the note under [Architecture](#architecture--package-structure)
- `fixtures/`: sample bank statements for manually trying the app (see README "Try it with sample data")
- `docs/canonical-template.md`: the column spec for the built-in canonical CSV/XLS/XLSX import format
- `docs/adding-a-parser.md`: how to add a parser for a bank's raw export format
- `docs/SECURITY.md`: deployment posture, what's done with data, vulnerability reporting
- `.github/workflows/ci.yml`: lint + format + test gate, required on `main`
- `.github/workflows/ai-commit-check.yml`: enforces the `[AI]` commit prefix rule below, required on `main`

<br>

## Commits

- Never run `git commit` or push unless the user explicitly asks for that commit, in that message.
  Preparing a diff is not permission to commit it.
- Subject line: `type: short description` (`fix:`, `feat:`, `refactor:`), matching [docs/CONTRIBUTING.md](CONTRIBUTING.md).
- End every AI-authored commit message with a `Co-Authored-By:` trailer naming the model.
  Prefix the subject with `[AI] `. Example:

  ```
  [AI] fix: this is an example commit message 

  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

  `.github/workflows/ai-commit-check.yml` enforces the prefix on every PR.
  A commit with the trailer but no `[AI]` prefix fails CI and blocks merge.

<br>

## Pull requests and issues

- Never open a pull request or a GitHub issue.
  Both are human actions.
  Prepare the branch and the commits; the user opens the PR and files the issue themselves.

<br>

## Troubleshooting

- **`ruff format --check` fails in CI but you didn't touch that file**: run `uv run ruff format .` locally and diff: a prior change likely left blank-line spacing ruff's formatter wants normalized. This has happened after large reorder-only refactors.
- **A test can't find a table/column**: check `schema.sql` was actually updated and that you're not relying on a migration: there isn't one. A stale local `accounting.db` from before a schema change needs to be deleted or reinitialized.
- **Import behaves differently than expected**: confirm which parser is selected for the account (per-account parser mapping, see `docs/adding-a-parser.md`) before assuming the resolution ladder is at fault.
