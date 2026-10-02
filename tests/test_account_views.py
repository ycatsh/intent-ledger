"""The account and payee profile pages, including their split sub-rows.

Both are rendered from the same template, so a change that suits one can
easily break the other - and neither is reachable from a plain index page.
"""

import pytest
from test_rules_overrides import FakeForm

from intent_ledger.accounting.accounts import get_account_view_page, get_active_accounts, get_payee_view_page
from intent_ledger.accounting.budget import shift_year_month
from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.rules_overrides import save_split
from intent_ledger.settings import today


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


def test_views_average_the_net_of_the_last_twelve_months(conn, account_factory, transaction_factory):
    checking = account_factory("Test Checking")
    food = account_factory("Test Food", type="expense")
    shop = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) VALUES ('Shop', 'shop', ?)", (food,)
    ).lastrowid
    today_ = today()
    for back in range(36):
        year, month = shift_year_month(today_.year, today_.month, -back)
        transaction_factory(checking, f"{year}-{month:02d}-01", -100000, "SHOP")
    conn.execute("UPDATE transactions SET payee_id = ?", (shop,))
    conn.commit()
    rebuild_ledger()

    assert get_account_view_page(food)["account"]["avg_month"] == -1000.0
    assert get_payee_view_page(shop)["account"]["avg_month"] == -1000.0


def test_net_worth_keeps_an_inactive_account_that_still_holds_money(
    conn, account_factory, transaction_factory
):
    closed = account_factory("Old Bank", is_active=0)
    account_factory("Empty Bank", is_active=0)
    transaction_factory(closed, "2026-01-01", 5000, "DEPOSIT")
    rebuild_ledger()

    balances = {a["name"]: a["balance"] for a in get_active_accounts()}

    assert balances["Old Bank"] == 50.0
    assert "Empty Bank" not in balances
