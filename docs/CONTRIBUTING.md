# Contributing

Thanks for considering to contribute to intent-ledger. It is my first serious OSS, so I appreciate it. This is a small, personal-finance app, so correctness and simplicity are weighted above everything.

<br>

## Setting up

```sh
uv sync --all-extras
```

Front-end dependencies are committed files:

| Dependency | Purpose |
|---|---|
| Tailwind CSS | Compiles `input.css` into `app.css` |
| Chart.js | Charts on Budget, Expenses, and Projects |
| Inter | UI font |
| IBM Plex Mono | Numbers and code |

Edit `static/css/input.css` to change styles. `app.css` is generated via Tailwind CSS [Standalone CLI](https://tailwindcss.com/blog/standalone-cli).

<br>

## Making changes

- **Commit messages**: `fix: <short-description>` or `feat: <short-description>` is fine; not strictly enforced.
- **Front-end assets**: never add a CDN `<script>` or `<link>`. Vendor the file under `static/` and reference it locally instead.
- **Tests**: include a test for any behavior change. This is not optional for changes to the importer (`intent_ledger/importer/`) or the ledger compiler (`intent_ledger/accounting/ledger.py`)

<br>

## Priorities for review

Correctness bugs in the importer and the ledger compiler are the highest-severity class of bug, since they can misrepresent someone's money.

<br>

## Reporting bugs

Open a GitHub issue using the bug report template. For anything that could expose financial data (not just a local bug), see [Reporting a vulnerability](SECURITY.md#reporting-a-vulnerability) instead of filing a public issue.

<br>

## Code of Conduct

Just be respectful and emphatetic.
