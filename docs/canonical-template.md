# Canonical import template

If your bank doesn't have a dedicated parser (see [adding-a-parser.md](adding-a-parser.md)) or if you just want a simple system, export your statement as CSV, XLSX, or even XLS and reshape it to this format before uploading.

<br>

## Columns

Header row required, column names case-insensitive, extra columns ignored.

| Column        | Required | Format                          | Notes |
|---------------|----------|----------------------------------|-------|
| `date`        | yes      | `YYYY-MM-DD`                     | Row is rejected (not the whole file) if unparseable. Every date in a file must use one format. |
| `description` | yes      | free text                        | Whatever your bank calls the transaction. |
| `withdrawal`  | yes      | unsigned decimal, max 2 dp       | Money out. Leave blank on deposit rows. A negative value is rejected. |
| `deposit`     | yes      | unsigned decimal, max 2 dp       | Money in. Leave blank on withdrawal rows. A negative value is rejected. |
| `balance`     | no       | signed decimal, max 2 dp         | The running balance *after* this transaction. |

Each row must have exactly one of `withdrawal`/`deposit` filled in, never
both and never neither. Amounts are plain numbers like `1234.50`, without
thousands separators.

Other date formats such as `DD/MM/YYYY` and `MM/DD/YYYY` also work, as long as
the whole file uses the same one. The importer picks the format that reads the
most dates in the file, and reads an ambiguous file like `03/04/2026` day-first.

Two identical rows are two real payments, so both are imported. Re-importing a
file only skips the copies already stored.

<br>

## Example

```csv
date,description,withdrawal,deposit,balance
2026-01-05,COFFEE SHOP,4.50,,995.50
2026-01-06,PAYCHECK,,1500.00,2495.50
```

<br>

## Account selection

The canonical importer never guesses which account a statement belongs to. Pick the account explicitly in the Import page before uploading. 
