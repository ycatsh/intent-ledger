# Adding a bank-specific parser

The [canonical CSV/XLS/XLSX template](canonical-template.md) works for any bank, but requires manually reformatting your exported sheets. If your bank's raw export has a stable, parseable structure, you can add a parser that reads it directly.

<br>

## The interface

`intent_ledger/importer/parsers/base.py` defines the contract:

```python
class StatementParser(Protocol):
    slug: str  # e.g. "chase", "wells-fargo"
    display_name: str  # shown in the Import page's format dropdown
    file_extensions: tuple[str, ...]  # e.g. (".csv",) or (".xlsx",)

    def sniff(self, path: Path) -> bool: ...
    def parse(self, path: Path) -> ParsedStatement: ...
```

- `sniff(path)` is a cheap check ("does this look like a file this parser can
  read?") used to validate uploads before a full parse.
- `parse(path)` returns a `ParsedStatement`:
  ```python
  class ParsedTransaction(TypedDict):
      posted_date: date
      raw_description: str
      amount_cents: int
      balance_cents: int | None


  class ParsedStatement(TypedDict):
      transactions: list[ParsedTransaction]
      row_errors: list[str]
  ```

Read the reference implementation first: `intent_ledger/importer/parsers/canonical.py`

<br>

## Steps

1. Create `intent_ledger/importer/parsers/<your_bank>.py` implementing `StatementParser`.
2. Register it in `intent_ledger/importer/parsers/__init__.py`'s `PARSERS` dict.
3. Add a constructed sample statement (not your real bank statement) under
   `fixtures/<your_bank>/`.
4. Add a regression test in `tests/` that parses it and asserts the expected
   transactions.

<br>

## What's parser-specific vs. shared

The description normalization and dedup fingerprinting in `intent_ledger/importer/normalize.py` are shared by every parser. Only the raw-file-to-`ParsedTransaction` translation belongs in your parser.

Account selection is never a parser concern. The importer always receives
an explicit `account_id` from the import statements page.
