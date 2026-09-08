"""The account and payee profile pages, including their split sub-rows.

Both are rendered from the same template, so a change that suits one can
easily break the other - and neither is reachable from a plain index page.
"""

import pytest
from test_rules_overrides import FakeForm

from intent_ledger.accounting.accounts import get_account_view_page, get_payee_view_page
from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.rules_overrides import save_split


@pytest.fixture
def split_transaction(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    dining_id = account_factory("Test Dining", type="expense")

    conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) VALUES (?, ?, ?)",
        ("Test Store", "test store", groceries_id),
    )
    payee_id = conn.execute("SELECT id FROM payees WHERE canonical_name = 'Test Store'").fetchone()["id"]

    txn_hash = transaction_factory(checking_id, "2026-01-05", -2000, "TEST STORE")
    conn.execute("UPDATE transactions SET payee_id = ? WHERE transaction_hash = ?", (payee_id, txn_hash))
    conn.commit()

    save_split(
        FakeForm(
            {
                "transaction_hash": txn_hash,
                "split_account": [groceries_id, dining_id],
                "split_amount": [12.0, 8.0],
                "split_note": ["food", "treat"],
            }
        )
    )
    rebuild_ledger()

    return {"account_id": checking_id, "payee_id": payee_id, "hash": txn_hash}


def test_account_view_page_builds(split_transaction):
    page = get_account_view_page(split_transaction["account_id"])

    assert page is not None
    assert len(page["charts"]["monthly_flow"]["labels"]) == 12


def test_payee_view_page_builds(split_transaction):
    """Regression: this passed the `today` function itself where a date was
    wanted, so every payee profile raised AttributeError.
    """
    page = get_payee_view_page(split_transaction["payee_id"])

    assert page is not None
    assert len(page["charts"]["monthly_flow"]["labels"]) == 12
    assert page["account"]["name"] == "Test Store"


def test_missing_account_and_payee_return_none(conn):
    assert get_account_view_page(999_999) is None
    assert get_payee_view_page(999_999) is None


@pytest.mark.parametrize("view", ["account", "payee"])
def test_profile_pages_carry_split_legs(split_transaction, view):
    page = (
        get_account_view_page(split_transaction["account_id"])
        if view == "account"
        else get_payee_view_page(split_transaction["payee_id"])
    )

    txn = next(t for t in page["recent_transactions"] if t["transaction_hash"] == split_transaction["hash"])

    assert txn["has_split"]
    assert {s["note"] for s in txn["splits"]} == {"food", "treat"}
    assert len(txn["splits"]) == 2
