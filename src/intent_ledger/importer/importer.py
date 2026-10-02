import re
import sqlite3
from collections import Counter
from pathlib import Path

from intent_ledger.db import db
from intent_ledger.importer.normalize import fingerprint, normalize_description
from intent_ledger.importer.parsers import PARSERS


def import_directory(directory: str | Path, account_id: int, parser_slug: str = "canonical"):
    directory = Path(directory)
    parser = PARSERS[parser_slug]

    summaries = []

    for extension in parser.file_extensions:
        for file_path in sorted(directory.glob(f"*{extension}")):
            summary = import_statement(file_path, account_id, parser_slug)
            summaries.append(summary)

    return summaries


def import_statement(statement_path: str | Path, account_id: int, parser_slug: str = "canonical"):
    """Insert a statement's rows, skipping the ones already stored.

    Identical rows are real: two coffees on the same day are two payments. So
    a row counts as already stored only when the account holds at least as
    many copies of it as the file has seen so far.
    """
    statement_path = Path(statement_path)
    parser = PARSERS[parser_slug]

    statement = parser.parse(statement_path)
    transactions = statement["transactions"]
    if not statement["row_errors"]:
        _check_running_balance(transactions)
    results = []

    with db.transaction() as conn:
        _ensure_account_active(conn, account_id)

        stored = _stored_counts(conn, account_id)
        seen = Counter()

        for txn in transactions:
            key = _dedup_key(
                txn["posted_date"], txn["amount_cents"], txn["balance_cents"], txn["raw_description"]
            )
            seen[key] += 1
            result = _process_transaction(conn, account_id, txn, occurrence=seen[key], stored=stored[key])
            results.append(result)

    inserted = sum(1 for r in results if r["status"] == "inserted")
    duplicates = sum(1 for r in results if r["status"] == "duplicate")
    unresolved = sum(1 for r in results if r["status"] == "inserted" and r["payee_id"] is None)

    return {
        "statement": str(statement_path),
        "total_transactions": len(results),
        "inserted": inserted,
        "duplicates": duplicates,
        "unresolved": unresolved,
        "row_errors": statement["row_errors"],
        "results": results,
    }


def _check_running_balance(transactions):
    if len(transactions) < 2 or any(txn["balance_cents"] is None for txn in transactions):
        return

    break_at = _balance_break(transactions)
    if break_at is not None and _balance_break(transactions[::-1]) is not None:
        txn = transactions[break_at]
        raise ValueError(
            f"The running balance doesn't add up at the {txn['posted_date'].isoformat()} row "
            f"'{txn['raw_description']}'. Check the file for a missing or mis-signed row."
        )


def _balance_break(transactions) -> int | None:
    for index in range(1, len(transactions)):
        previous, current = transactions[index - 1], transactions[index]
        if current["balance_cents"] != previous["balance_cents"] + current["amount_cents"]:
            return index
    return None


def _ensure_account_active(conn, account_id: int) -> None:
    row = conn.execute(
        "SELECT 1 FROM accounts WHERE id = ? AND is_active = 1",
        (account_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Account {account_id} not found or inactive.")


def _stored_counts(conn, account_id: int) -> Counter:
    rows = conn.execute(
        """
        SELECT posted_date, amount_cents, balance_cents, raw_description
        FROM transactions
        WHERE account_id = ? AND status = 'bank'
        """,
        (account_id,),
    )

    return Counter(
        _dedup_key(row["posted_date"], row["amount_cents"], row["balance_cents"], row["raw_description"])
        for row in rows
    )


def _dedup_key(posted_date, amount_cents: int, balance_cents: int | None, raw_description: str) -> tuple:
    return str(posted_date), amount_cents, balance_cents, _content(raw_description)


def _process_transaction(conn, account_id: int, txn: dict, occurrence: int, stored: int):
    normalized = normalize_description(txn["raw_description"])

    transaction_hash = _resolve_transaction_hash(conn, account_id, txn, normalized, occurrence)
    payee_id = _resolve_payee(conn, normalized["payee"])

    if occurrence <= stored:
        return {"status": "duplicate", "transaction_hash": transaction_hash, "payee_id": payee_id}

    _insert_transaction(
        conn,
        {
            "account_id": account_id,
            "posted_date": str(txn["posted_date"]),
            "amount_cents": txn["amount_cents"],
            "payee_id": payee_id,
            "raw_description": txn["raw_description"],
            "normalized_description": normalized["payee"],
            "note": normalized["note"],
            "transaction_hash": transaction_hash,
            "balance_cents": txn["balance_cents"],
        },
    )

    return {"status": "inserted", "transaction_hash": transaction_hash, "payee_id": payee_id}


def _resolve_transaction_hash(conn, account_id: int, txn: dict, normalized: dict, occurrence: int) -> str:
    primary_hash = fingerprint(
        account_id=account_id,
        posted_date=str(txn["posted_date"]),
        amount_cents=txn["amount_cents"],
        balance_cents=txn["balance_cents"],
        description="",
    )

    existing = conn.execute(
        "SELECT raw_description FROM transactions WHERE transaction_hash = ?",
        (primary_hash,),
    ).fetchone()

    if existing is None or _content(existing["raw_description"]) == _content(txn["raw_description"]):
        first_copy_hash = primary_hash
    else:
        first_copy_hash = fingerprint(
            account_id=account_id,
            posted_date=str(txn["posted_date"]),
            amount_cents=txn["amount_cents"],
            balance_cents=txn["balance_cents"],
            description=normalized["structured"],
        )

    return first_copy_hash if occurrence == 1 else f"{first_copy_hash}#{occurrence}"


def _content(description: str) -> str:
    return re.sub(r"\s+", "", description).upper()


def _resolve_payee(conn, normalized_description: str):
    row = conn.execute(
        """
        SELECT payee_id
        FROM payee_aliases
        WHERE normalized_alias = ?
        LIMIT 1
        """,
        (normalized_description,),
    ).fetchone()

    if row is None:
        return None

    return row["payee_id"]


def _insert_transaction(conn, txn: dict) -> None:
    try:
        conn.execute(
            """
            INSERT INTO transactions (
                account_id,
                posted_date,
                amount_cents,
                payee_id,
                raw_description,
                normalized_description,
                note,
                transaction_hash,
                balance_cents
            )
            VALUES (
                :account_id,
                :posted_date,
                :amount_cents,
                :payee_id,
                :raw_description,
                :normalized_description,
                :note,
                :transaction_hash,
                :balance_cents
            )
            """,
            txn,
        )
    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"{txn['posted_date']} {txn['raw_description']!r} clashes with a stored row: {e}"
        ) from e
