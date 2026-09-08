from intent_ledger.accounting.payees import get_or_create_payee, normalize
from intent_ledger.accounting.repositories.accounts import AccountRepository
from intent_ledger.accounting.repositories.payees import PayeeRepository
from intent_ledger.accounting.repositories.rules_overrides import OverrideRepository
from intent_ledger.db import db
from intent_ledger.importer.normalize import manual_fingerprint


def add_manual_transaction(fields: dict):
    from_account_id = fields.get("from_account_id")
    category_account_id = fields.get("category_account_id")
    payee_name = (fields.get("payee_name") or "").strip()
    posted_date = fields.get("posted_date")
    amount = fields.get("amount")

    if not from_account_id:
        raise ValueError("Account is required.")
    if not category_account_id:
        raise ValueError("Category is required.")
    if from_account_id == category_account_id:
        raise ValueError("Account and category must differ.")
    if not posted_date:
        raise ValueError("Date is required.")
    if not amount:
        raise ValueError("Amount is required.")

    amount_cents = round(amount * 100)
    if amount_cents == 0:
        raise ValueError("Amount must be non-zero.")

    with db.transaction() as conn:
        payee_id = None

        if payee_name:
            payee_id = get_or_create_payee(conn, payee_name, normalize(payee_name), category_account_id)

        _insert_manual_transaction(
            conn,
            from_account_id=from_account_id,
            category_account_id=category_account_id,
            payee_id=payee_id,
            posted_date=posted_date,
            amount_cents=amount_cents,
            description=payee_name,
        )


def _insert_manual_transaction(
    conn,
    from_account_id: int,
    category_account_id: int,
    posted_date,
    amount_cents: int,
    description: str,
    payee_id: int | None = None,
):
    account_repo = AccountRepository(conn)

    from_account = account_repo.get(from_account_id)
    if from_account is None:
        raise ValueError(f"Account {from_account_id} not found.")

    if account_repo.get(category_account_id) is None:
        raise ValueError(f"Account {category_account_id} not found.")

    if payee_id is not None and PayeeRepository(conn).get(payee_id) is None:
        raise ValueError(f"Payee {payee_id} not found.")

    transaction_hash = manual_fingerprint()

    conn.execute(
        """
        INSERT INTO transactions (
            account_id,
            posted_date,
            amount_cents,
            payee_id,
            raw_description,
            normalized_description,
            transaction_hash,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'manual')
        """,
        (
            from_account_id,
            posted_date,
            -amount_cents,
            payee_id,
            description,
            description,
            transaction_hash,
        ),
    )

    OverrideRepository(conn).set(transaction_hash, category_account_id)


def delete_manual_transaction(transaction_hash: str):
    with db.transaction() as conn:
        txn = conn.execute(
            "SELECT id, status FROM transactions WHERE transaction_hash = ?",
            (transaction_hash,),
        ).fetchone()

        if txn is None:
            raise ValueError("Transaction not found.")
        if txn["status"] != "manual":
            raise ValueError("Only manually entered transactions can be deleted here.")

        conn.execute("DELETE FROM ledger WHERE transaction_id = ?", (txn["id"],))
        OverrideRepository(conn).delete(transaction_hash)
        conn.execute("DELETE FROM transactions WHERE id = ?", (txn["id"],))
