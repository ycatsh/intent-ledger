from intent_ledger.accounting.repositories.rules_overrides import OverrideRepository
from intent_ledger.accounting.repositories.rules_splits import SplitLine, SplitRepository
from intent_ledger.accounting.rules import default_account_id, fetch_account_rules
from intent_ledger.db import db
from intent_ledger.domain.money import Money


def get_override_account_id(conn, transaction_hash):
    return OverrideRepository(conn).get_account_id(transaction_hash)


def get_overrides():
    with db.transaction() as conn:
        rows = conn.execute("""
            SELECT
                o.transaction_hash,
                o.account_id,
                a.name AS account_name,
                t.posted_date,
                t.note,
                t.amount_cents / 100.0 AS amount,
                t.account_id AS from_account_id,
                t.raw_description,
                t.payee_id,
                m.canonical_name AS payee,
                0 AS has_split
            FROM transactions_overrides o
            JOIN transactions t
                ON t.transaction_hash = o.transaction_hash
            JOIN accounts a
                ON a.id = o.account_id
            LEFT JOIN payees m
                ON m.id = t.payee_id

            UNION ALL

            SELECT
                s.transaction_hash,
                NULL AS account_id,
                GROUP_CONCAT(DISTINCT a.name) AS account_name,
                t.posted_date,
                t.note,
                t.amount_cents / 100.0 AS amount,
                t.account_id AS from_account_id,
                t.raw_description,
                t.payee_id,
                m.canonical_name AS payee,
                1 AS has_split
            FROM transactions_splits s
            JOIN transactions t
                ON t.transaction_hash = s.transaction_hash
            JOIN accounts a
                ON a.id = s.account_id
            LEFT JOIN payees m
                ON m.id = t.payee_id
            GROUP BY s.transaction_hash

            ORDER BY posted_date DESC
        """).fetchall()

        accounts_by_id = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM accounts")}
        rules = fetch_account_rules(conn)

        for row in rows:
            row["default_account_name"] = None
            row["from_account_name"] = accounts_by_id.get(row["from_account_id"])

            if row["has_split"]:
                continue

            default_id = default_account_id(rules, conn, row["raw_description"], row["payee_id"])

            if default_id != row["account_id"]:
                row["default_account_name"] = accounts_by_id.get(default_id)

        return rows


def get_override_context(transaction_hash):
    with db.transaction() as conn:
        row = conn.execute(
            """
            SELECT
                t.transaction_hash,
                t.posted_date,
                t.note,
                t.amount_cents / 100.0 AS amount,
                t.account_id AS from_account_id,
                t.raw_description,
                t.payee_id,
                m.canonical_name AS payee,
                o.id AS override_id,
                o.account_id AS override_account_id
            FROM transactions t
            LEFT JOIN payees m
                ON m.id = t.payee_id
            LEFT JOIN transactions_overrides o
                ON o.transaction_hash = t.transaction_hash
            WHERE t.transaction_hash = ?
        """,
            (transaction_hash,),
        ).fetchone()

        if row is None:
            return None

        splits = SplitRepository(conn).list_for_transaction(transaction_hash)

        default_id = default_account_id(
            fetch_account_rules(conn), conn, row["raw_description"], row["payee_id"]
        )

        default_account_name = None
        if default_id != row["override_account_id"]:
            default_row = conn.execute("SELECT name FROM accounts WHERE id = ?", (default_id,)).fetchone()
            default_account_name = default_row["name"] if default_row else None

    return {
        "id": row["override_id"],
        "transaction_hash": row["transaction_hash"],
        "posted_date": row["posted_date"],
        "note": row["note"],
        "amount": row["amount"],
        "payee": row["payee"],
        "raw_description": row["raw_description"],
        "account_id": row["override_account_id"],
        "has_override": row["override_id"] is not None,
        "default_account_name": default_account_name,
        "splits": [
            {"account_id": s["account_id"], "amount": Money(s["amount_cents"]).amount, "note": s["note"]}
            for s in splits
        ],
        "has_split": len(splits) > 0,
    }


def add_override(form):
    transaction_hash = form.get("transaction_hash", "").strip()
    account_id = form.get("account_id", type=int)

    if not transaction_hash:
        raise ValueError("Select a transaction to override.")
    if not account_id:
        raise ValueError("Account is required.")

    with db.transaction() as conn:
        SplitRepository(conn).delete(transaction_hash)
        OverrideRepository(conn).set(transaction_hash, account_id)


def delete_override(transaction_hash):
    with db.transaction() as conn:
        OverrideRepository(conn).delete(transaction_hash)
        SplitRepository(conn).delete(transaction_hash)


def save_split(form):
    transaction_hash = form.get("transaction_hash", "").strip()
    if not transaction_hash:
        raise ValueError("Select a transaction to split.")

    lines = parse_split_lines(form)

    if len(lines) < 2:
        raise ValueError("A split needs at least two lines.")

    with db.transaction() as conn:
        txn = conn.execute(
            "SELECT amount_cents FROM transactions WHERE transaction_hash = ?",
            (transaction_hash,),
        ).fetchone()

        if txn is None:
            raise ValueError("Transaction not found.")

        target_total = -txn["amount_cents"]
        total = sum(amount_cents for _, amount_cents, _ in lines)

        if total != target_total:
            raise ValueError(
                f"Split amounts must add up to {Money(target_total)}, got {Money(total)} "
                f"({Money(target_total - total)} left to balance)."
            )

        OverrideRepository(conn).delete(transaction_hash)
        SplitRepository(conn).replace(transaction_hash, lines)


def parse_split_lines(form) -> list[SplitLine]:
    """Read the split rows off a form, skipping any the user left blank."""
    accounts = form.getlist("split_account")
    amounts = form.getlist("split_amount")
    notes = form.getlist("split_note")

    lines = []

    for index, raw_account in enumerate(accounts):
        raw_amount = amounts[index].strip() if index < len(amounts) else ""
        note = notes[index].strip() if index < len(notes) else ""

        if not raw_account.strip() and not raw_amount:
            continue

        if not raw_account.strip():
            raise ValueError("Every split line needs a category.")

        try:
            amount = float(raw_amount)
        except ValueError:
            raise ValueError("Every split line needs an amount.") from None

        lines.append((int(raw_account), Money.from_dollars(amount).cents, note or None))

    return lines
