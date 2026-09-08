import re
import sqlite3
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
    statement_path = Path(statement_path)
    parser = PARSERS[parser_slug]

    statement = parser.parse(statement_path)
    transactions = statement["transactions"]
    results = []

    with db.transaction() as conn:
        _ensure_account_active(conn, account_id)

        for txn in transactions:
            result = _process_transaction(conn=conn, account_id=account_id, txn=txn)
            results.append(result)

    inserted = sum(1 for r in results if r["status"] == "inserted")
    duplicates = sum(1 for r in results if r["status"] == "duplicate")
    unresolved = sum(1 for r in results if r.get("payee_id") is None)

    return {
        "statement": str(statement_path),
        "total_transactions": len(results),
        "inserted": inserted,
        "duplicates": duplicates,
        "unresolved": unresolved,
        "row_errors": statement["row_errors"],
        "results": results,
    }


def _ensure_account_active(conn, account_id: int) -> None:
    row = conn.execute(
        "SELECT 1 FROM accounts WHERE id = ? AND is_active = 1",
        (account_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Account {account_id} not found or inactive.")


def _process_transaction(conn, account_id: int, txn: dict):
    normalized = normalize_description(txn["raw_description"])

    transaction_hash = _resolve_transaction_hash(conn, account_id, txn, normalized)
    payee_id = _resolve_payee(conn, normalized["payee"])

    inserted = _insert_transaction(
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

    return {
        "status": "inserted" if inserted else "duplicate",
        "transaction_hash": transaction_hash,
        "payee_id": payee_id,
    }


def _resolve_transaction_hash(conn, account_id: int, txn: dict, normalized: dict) -> str:
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

    if existing is None or _same_content(existing["raw_description"], txn["raw_description"]):
        return primary_hash

    return fingerprint(
        account_id=account_id,
        posted_date=str(txn["posted_date"]),
        amount_cents=txn["amount_cents"],
        balance_cents=txn["balance_cents"],
        description=normalized["structured"],
    )


def _same_content(a: str, b: str) -> bool:
    def strip_ws(s: str) -> str:
        return re.sub(r"\s+", "", s).upper()

    return strip_ws(a) == strip_ws(b)


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


def _insert_transaction(conn, txn: dict) -> bool:
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

        return True
    except sqlite3.IntegrityError as exc:
        if "transaction_hash" not in str(exc):
            raise

        return False
