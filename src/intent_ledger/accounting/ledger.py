import uuid
from collections import defaultdict

from intent_ledger.accounting.repositories.rules_splits import SplitRepository
from intent_ledger.accounting.rules import default_account_id, fetch_account_rules, find_matching_rule
from intent_ledger.accounting.rules_overrides import get_override_account_id
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

    changed_category = {
        txn_id for txn_id, accounts in after_categories.items() if accounts != before_categories.get(txn_id)
    }
    changed_payee = {
        txn_id for txn_id, payee_id in after_payees.items() if payee_id != before_payees.get(txn_id)
    }

    return len(changed_category | changed_payee)


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

    counterpart = transfer_pairs.get(txn["id"])
    if counterpart and txn["amount_cents"] > 0:
        return

    group_id = uuid.uuid4().hex

    _create_entry(
        conn,
        group_id,
        txn["id"],
        txn["account_id"],
        txn["amount_cents"],
        txn["raw_description"],
    )

    if counterpart:
        _create_entry(
            conn,
            group_id,
            txn["id"],
            counterpart["account_id"],
            -txn["amount_cents"],
            txn["raw_description"],
        )
        return

    splits = SplitRepository(conn).list_for_transaction(txn["transaction_hash"])
    if splits:
        for split in splits:
            _create_entry(
                conn,
                group_id,
                txn["id"],
                split["account_id"],
                split["amount_cents"],
                split["note"] or txn["raw_description"],
            )
        return

    to_account_id = _resolve_target_account(conn, txn, rules)
    _create_entry(
        conn,
        group_id,
        txn["id"],
        to_account_id,
        -txn["amount_cents"],
        txn["raw_description"],
    )


def _backfill_rule_payee(conn, txn, rules):
    rule = find_matching_rule(rules, txn["raw_description"])
    payee_id = rule["payee_id"] if rule else None
    if payee_id is None or txn["payee_id"] == payee_id:
        return

    conn.execute("UPDATE transactions SET payee_id = ? WHERE id = ?", (payee_id, txn["id"]))


def _resolve_target_account(conn, txn, rules):
    return get_override_account_id(conn, txn["transaction_hash"]) or default_account_id(
        rules, conn, txn["raw_description"], txn["payee_id"]
    )


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
