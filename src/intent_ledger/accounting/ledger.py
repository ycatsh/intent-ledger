import uuid
from collections import defaultdict

from intent_ledger.accounting import resolution
from intent_ledger.accounting.rules import fetch_account_rules, find_matching_rule
from intent_ledger.accounting.rules_transfers import (
    fetch_transfer_rules,
    is_transfer_candidate,
    match_transfers,
)
from intent_ledger.db import db


def rebuild_ledger() -> int:
    with db.transaction() as conn:
        before_categories = _category_snapshot(conn)
        before_payees = _payee_snapshot(conn)

        _clear_ledger(conn)

        rules = fetch_account_rules(conn)
        transactions = _load_transactions(conn)
        transfer_pairs = _match_transfer_pairs(conn, transactions)

        for txn in transactions:
            _create_group_for_transaction(conn, txn, rules, transfer_pairs)

        _validate_all_groups(conn)

        after_categories = _category_snapshot(conn)
        after_payees = _payee_snapshot(conn)

    return _count_changed_transactions(before_categories, after_categories, before_payees, after_payees)


def _clear_ledger(conn):
    conn.execute("DELETE FROM ledger")


def _load_transactions(conn):
    return conn.execute("""
        SELECT *
        FROM transactions
        ORDER BY posted_date, id
    """).fetchall()


def _match_transfer_pairs(conn, transactions):
    transfer_rules = fetch_transfer_rules(conn)
    candidates = [t for t in transactions if is_transfer_candidate(transfer_rules, t["raw_description"])]
    return match_transfers(candidates)


def _create_group_for_transaction(conn, txn, rules, transfer_pairs):
    _backfill_rule_payee(conn, txn, rules)

    counter_entries = resolution.resolve(conn, txn, rules, transfer_pairs)
    if counter_entries is resolution.SKIP:
        return

    group_id = uuid.uuid4().hex
    _create_entry(conn, group_id, txn["id"], txn["account_id"], txn["amount_cents"], txn["raw_description"])
    for entry in counter_entries:
        _create_entry(conn, group_id, txn["id"], entry.account_id, entry.amount_cents, entry.description)


def _backfill_rule_payee(conn, txn, rules):
    rule = find_matching_rule(rules, txn["raw_description"])
    payee_id = rule["payee_id"] if rule else None
    if payee_id is None or txn["payee_id"] == payee_id:
        return

    conn.execute("UPDATE transactions SET payee_id = ? WHERE id = ?", (payee_id, txn["id"]))


def _create_entry(
    conn,
    group_id,
    transaction_id,
    account_id,
    amount_cents,
    description=None,
):
    conn.execute(
        """
        INSERT INTO ledger(
            group_id,
            transaction_id,
            account_id,
            amount_cents,
            description
        )
        VALUES(
            :group_id,
            :transaction_id,
            :account_id,
            :amount_cents,
            :description
        )
    """,
        {
            "group_id": group_id,
            "transaction_id": transaction_id,
            "account_id": account_id,
            "amount_cents": amount_cents,
            "description": description,
        },
    )


def _validate_all_groups(conn):
    groups = _find_unbalanced_groups(conn)
    if groups:
        raise ValueError(f"{len(groups)} groups unbalanced")


def _find_unbalanced_groups(conn):
    return conn.execute("""
        SELECT
            group_id,
            SUM(amount_cents) balance
        FROM ledger
        GROUP BY group_id
        HAVING balance!=0
    """).fetchall()



# Bookkeeping for rebuild_ledger:

def _category_snapshot(conn):
    rows = conn.execute("""
        SELECT l.transaction_id, l.account_id
        FROM ledger l
        JOIN transactions t ON t.id = l.transaction_id
        WHERE l.account_id != t.account_id
    """).fetchall()

    snapshot = defaultdict(set)
    for row in rows:
        snapshot[row["transaction_id"]].add(row["account_id"])

    return snapshot


def _payee_snapshot(conn):
    rows = conn.execute("SELECT id, payee_id FROM transactions").fetchall()
    return {row["id"]: row["payee_id"] for row in rows}


def _count_changed_transactions(before_categories, after_categories, before_payees, after_payees):
    changed_category = {
        txn_id for txn_id, accounts in after_categories.items() if accounts != before_categories.get(txn_id)
    }
    changed_payee = {
        txn_id for txn_id, payee_id in after_payees.items() if payee_id != before_payees.get(txn_id)
    }
    return len(changed_category | changed_payee)
