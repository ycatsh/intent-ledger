import re

import pytest

from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.rules_overrides import (
    add_override,
    delete_override,
    get_override_account_id,
    save_split,
)


class FakeForm(dict):
    def get(self, key, default=None, type=None):
        if key not in self:
            return default
        value = super().get(key)
        if type is None:
            return value
        try:
            return type(value)
        except (TypeError, ValueError):
            return default

    def getlist(self, key, type=str):
        return [type(v) for v in self.get(key, [])]


def splits_for(conn, transaction_hash):
    rows = conn.execute(
        "SELECT account_id, amount_cents, note FROM transactions_splits "
        "WHERE transaction_hash = ? ORDER BY account_id",
        (transaction_hash,),
    ).fetchall()
    return [dict(r) for r in rows]


def test_add_override_creates_and_overwrites(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    dining_id = account_factory("Test Dining", type="expense")
    txn_hash = transaction_factory(checking_id, "2026-01-05", -1500, "SOME STORE")
    conn.commit()

    add_override(FakeForm({"transaction_hash": txn_hash, "account_id": groceries_id}))
    assert get_override_account_id(conn, txn_hash) == groceries_id

    add_override(FakeForm({"transaction_hash": txn_hash, "account_id": dining_id}))
    assert get_override_account_id(conn, txn_hash) == dining_id


def test_add_override_requires_transaction_hash_and_account(conn):
    with pytest.raises(ValueError):
        add_override(FakeForm({"transaction_hash": "", "account_id": 1}))

    with pytest.raises(ValueError):
        add_override(FakeForm({"transaction_hash": "abc", "account_id": None}))


def test_add_override_clears_any_existing_split(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    dining_id = account_factory("Test Dining", type="expense")
    txn_hash = transaction_factory(checking_id, "2026-01-05", -2000, "MIXED SPEND")
    conn.commit()

    save_split(
        FakeForm(
            {
                "transaction_hash": txn_hash,
                "split_account": [groceries_id, dining_id],
                "split_amount": [12.0, 8.0],
                "split_note": ["g", "d"],
            }
        )
    )
    assert len(splits_for(conn, txn_hash)) == 2

    add_override(FakeForm({"transaction_hash": txn_hash, "account_id": groceries_id}))

    assert splits_for(conn, txn_hash) == []
    assert get_override_account_id(conn, txn_hash) == groceries_id


def test_delete_override_removes_override_and_any_split(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    txn_hash = transaction_factory(checking_id, "2026-01-05", -1500, "SOME STORE")
    conn.commit()

    add_override(FakeForm({"transaction_hash": txn_hash, "account_id": groceries_id}))
    assert get_override_account_id(conn, txn_hash) == groceries_id

    delete_override(txn_hash)

    assert get_override_account_id(conn, txn_hash) is None


def test_save_split_requires_amounts_to_sum_to_the_transaction_amount(
    conn, account_factory, transaction_factory
):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    dining_id = account_factory("Test Dining", type="expense")
    txn_hash = transaction_factory(checking_id, "2026-01-05", -2000, "MIXED SPEND")
    conn.commit()

    with pytest.raises(ValueError, match=re.escape("must add up to 20.00, got 19.99")):
        save_split(
            FakeForm(
                {
                    "transaction_hash": txn_hash,
                    "split_account": [groceries_id, dining_id],
                    "split_amount": [11.99, 8.00],
                    "split_note": ["", ""],
                }
            )
        )

    assert splits_for(conn, txn_hash) == []


def test_save_split_requires_at_least_two_destination_accounts(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    txn_hash = transaction_factory(checking_id, "2026-01-05", -1500, "SOME STORE")
    conn.commit()

    with pytest.raises(ValueError):
        save_split(
            FakeForm(
                {
                    "transaction_hash": txn_hash,
                    "split_account": [groceries_id],
                    "split_amount": [15.00],
                    "split_note": [""],
                }
            )
        )


def test_save_split_rejects_unknown_transaction(conn, account_factory):
    groceries_id = account_factory("Test Groceries", type="expense")
    dining_id = account_factory("Test Dining", type="expense")

    with pytest.raises(ValueError, match="Transaction not found"):
        save_split(
            FakeForm(
                {
                    "transaction_hash": "does-not-exist",
                    "split_account": [groceries_id, dining_id],
                    "split_amount": [10.0, 10.0],
                    "split_note": ["", ""],
                }
            )
        )


def test_save_split_replaces_prior_split_and_clears_any_override(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    dining_id = account_factory("Test Dining", type="expense")
    housing_id = account_factory("Test Housing", type="expense")
    txn_hash = transaction_factory(checking_id, "2026-01-05", -2000, "MIXED SPEND")
    conn.commit()

    add_override(FakeForm({"transaction_hash": txn_hash, "account_id": housing_id}))
    assert get_override_account_id(conn, txn_hash) == housing_id

    save_split(
        FakeForm(
            {
                "transaction_hash": txn_hash,
                "split_account": [groceries_id, dining_id],
                "split_amount": [12.0, 8.0],
                "split_note": ["  groceries  ", ""],
            }
        )
    )

    assert get_override_account_id(conn, txn_hash) is None
    assert splits_for(conn, txn_hash) == [
        {"account_id": groceries_id, "amount_cents": 1200, "note": "groceries"},
        {"account_id": dining_id, "amount_cents": 800, "note": None},
    ]

    save_split(
        FakeForm(
            {
                "transaction_hash": txn_hash,
                "split_account": [groceries_id, dining_id, housing_id],
                "split_amount": [5.0, 5.0, 10.0],
                "split_note": ["", "", ""],
            }
        )
    )

    assert splits_for(conn, txn_hash) == [
        {"account_id": groceries_id, "amount_cents": 500, "note": None},
        {"account_id": dining_id, "amount_cents": 500, "note": None},
        {"account_id": housing_id, "amount_cents": 1000, "note": None},
    ]


def test_split_wins_over_override_and_rule_during_ledger_rebuild(conn, account_factory, transaction_factory):
    """A split determines the final posted accounts regardless of any
    override or rule that would otherwise apply - see ledger.py, where
    splits are checked before override/rule resolution.
    """
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    dining_id = account_factory("Test Dining", type="expense")
    other_id = account_factory("Test Other", type="expense")
    txn_hash = transaction_factory(checking_id, "2026-01-05", -2000, "MIXED SPEND")
    conn.commit()

    add_override(FakeForm({"transaction_hash": txn_hash, "account_id": other_id}))
    save_split(
        FakeForm(
            {
                "transaction_hash": txn_hash,
                "split_account": [groceries_id, dining_id],
                "split_amount": [12.0, 8.0],
                "split_note": ["", ""],
            }
        )
    )

    rebuild_ledger()

    legs = conn.execute(
        """
        SELECT l.account_id, l.amount_cents
        FROM ledger l
        JOIN transactions t ON t.id = l.transaction_id
        WHERE t.transaction_hash = ?
        ORDER BY l.account_id
        """,
        (txn_hash,),
    ).fetchall()

    accounts_hit = {row["account_id"] for row in legs}
    assert other_id not in accounts_hit
    assert accounts_hit == {checking_id, groceries_id, dining_id}
    assert sum(row["amount_cents"] for row in legs) == 0


def test_save_split_accepts_any_number_of_lines(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    categories = [account_factory(f"Test Cat {i}", type="expense") for i in range(4)]
    txn_hash = transaction_factory(checking_id, "2026-01-05", -4000, "BIG SHOP")
    conn.commit()

    save_split(
        FakeForm(
            {
                "transaction_hash": txn_hash,
                "split_account": categories,
                "split_amount": [10.0, 10.0, 10.0, 10.0],
                "split_note": ["a", "b", "c", "d"],
            }
        )
    )

    assert [row["amount_cents"] for row in splits_for(conn, txn_hash)] == [1000] * 4


def test_save_split_ignores_lines_left_blank(conn, account_factory, transaction_factory):
    """The editor lets rows be added freely, so an untouched row is not an
    error - it is just a row the user never filled in.
    """
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    dining_id = account_factory("Test Dining", type="expense")
    txn_hash = transaction_factory(checking_id, "2026-01-05", -2000, "MIXED SPEND")
    conn.commit()

    save_split(
        FakeForm(
            {
                "transaction_hash": txn_hash,
                "split_account": [groceries_id, dining_id, ""],
                "split_amount": [12.0, 8.0, ""],
                "split_note": ["", "", ""],
            }
        )
    )

    assert len(splits_for(conn, txn_hash)) == 2


def test_save_split_reports_how_much_is_left_to_balance(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    dining_id = account_factory("Test Dining", type="expense")
    txn_hash = transaction_factory(checking_id, "2026-01-05", -2000, "MIXED SPEND")
    conn.commit()

    with pytest.raises(ValueError, match=re.escape("(5.00 left to balance)")):
        save_split(
            FakeForm(
                {
                    "transaction_hash": txn_hash,
                    "split_account": [groceries_id, dining_id],
                    "split_amount": [10.0, 5.0],
                    "split_note": ["", ""],
                }
            )
        )


def test_save_split_rejects_a_line_missing_its_category_or_amount(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    txn_hash = transaction_factory(checking_id, "2026-01-05", -2000, "MIXED SPEND")
    conn.commit()

    with pytest.raises(ValueError, match="needs a category"):
        save_split(
            FakeForm(
                {
                    "transaction_hash": txn_hash,
                    "split_account": [groceries_id, ""],
                    "split_amount": [12.0, 8.0],
                    "split_note": ["", ""],
                }
            )
        )

    with pytest.raises(ValueError, match="needs an amount"):
        save_split(
            FakeForm(
                {
                    "transaction_hash": txn_hash,
                    "split_account": [groceries_id, groceries_id],
                    "split_amount": [12.0, "abc"],
                    "split_note": ["", ""],
                }
            )
        )
