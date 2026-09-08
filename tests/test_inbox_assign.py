import pytest

from intent_ledger.accounting.accounts import (
    get_all_account_names,
    get_all_accounts,
    get_payee_default_categories,
)
from intent_ledger.accounting.inbox import _assign
from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.rules import fetch_account_rules, find_matching_rule


def test_assign_without_payee_learns_an_account_rule_and_sets_an_override(
    conn, account_factory, transaction_factory
):
    checking_id = account_factory("Test Checking")
    account_factory("Test Home Loan", type="liability")
    transaction_hash = transaction_factory(
        checking_id, "2026-01-15", -50000, "FIRST NATIONAL HOME LOAN PAYMENT 12345"
    )
    transaction_id = conn.execute(
        "SELECT id FROM transactions WHERE transaction_hash = ?", (transaction_hash,)
    ).fetchone()["id"]

    _assign(conn, transaction_id, "", "Test Home Loan")

    rules = conn.execute(
        "SELECT match_type, pattern, priority FROM account_rules WHERE priority = 100"
    ).fetchall()
    assert len(rules) == 1
    assert rules[0]["match_type"] == "equals"
    assert rules[0]["pattern"] == "FIRST NATIONAL HOME"

    override = conn.execute(
        "SELECT account_id FROM transactions_overrides WHERE transaction_hash = ?", (transaction_hash,)
    ).fetchone()
    assert override is not None

    txn = conn.execute("SELECT payee_id FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    assert txn["payee_id"] is None


def test_reassigning_the_same_description_replaces_the_learned_rule_not_duplicates_it(
    conn, account_factory, transaction_factory
):
    checking_id = account_factory("Test Checking")
    account_factory("Test Home Loan", type="liability")
    other_id = account_factory("Test Other Expense", type="expense")

    hash_1 = transaction_factory(checking_id, "2026-01-15", -50000, "FIRST NATIONAL HOME LOAN PAYMENT 12345")
    id_1 = conn.execute("SELECT id FROM transactions WHERE transaction_hash = ?", (hash_1,)).fetchone()["id"]
    _assign(conn, id_1, "", "Test Home Loan")

    hash_2 = transaction_factory(checking_id, "2026-02-15", -50000, "FIRST NATIONAL HOME LOAN PAYMENT 12345")
    id_2 = conn.execute("SELECT id FROM transactions WHERE transaction_hash = ?", (hash_2,)).fetchone()["id"]
    _assign(conn, id_2, "", "Test Other Expense")

    rules = conn.execute(
        "SELECT account_id FROM account_rules "
        "WHERE match_type = 'equals' AND pattern = 'FIRST NATIONAL HOME' AND priority = 100"
    ).fetchall()
    assert len(rules) == 1
    assert rules[0]["account_id"] == other_id


def test_learned_rule_fires_on_a_later_transaction_with_a_different_reference_number(
    conn, account_factory, transaction_factory
):
    """Regression test: a learned rule's pattern is the fuzzy-extracted key
    (reference number stripped), and matching must compare against that same
    extracted key otherwise the rule can never fire again.
    """
    checking_id = account_factory("Test Checking")
    loan_id = account_factory("Test Home Loan", type="liability")

    first_hash = transaction_factory(
        checking_id, "2026-01-15", -50000, "FIRST NATIONAL HOME LOAN PAYMENT 12345"
    )
    first_id = conn.execute(
        "SELECT id FROM transactions WHERE transaction_hash = ?", (first_hash,)
    ).fetchone()["id"]
    _assign(conn, first_id, "", "Test Home Loan")

    transaction_factory(checking_id, "2026-02-15", -50000, "FIRST NATIONAL HOME LOAN PAYMENT 98765")
    rebuild_ledger()

    rules = fetch_account_rules(conn)
    rule = find_matching_rule(rules, "FIRST NATIONAL HOME LOAN PAYMENT 98765")
    assert rule is not None and rule["account_id"] == loan_id

    second = conn.execute(
        "SELECT l.account_id FROM ledger l "
        "JOIN transactions t ON t.id = l.transaction_id "
        "WHERE t.raw_description = 'FIRST NATIONAL HOME LOAN PAYMENT 98765' AND l.account_id != t.account_id"
    ).fetchone()
    assert second["account_id"] == loan_id


def test_assigning_a_payee_with_no_default_adopts_the_chosen_account(
    conn, account_factory, transaction_factory
):
    checking_id = account_factory("Test Checking")
    loan_id = account_factory("Test Home Loan", type="liability")
    transaction_hash = transaction_factory(
        checking_id, "2026-01-15", -50000, "FIRST NATIONAL HOME LOAN PAYMENT 12345"
    )
    transaction_id = conn.execute(
        "SELECT id FROM transactions WHERE transaction_hash = ?", (transaction_hash,)
    ).fetchone()["id"]

    payee_id = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) VALUES (?, ?, NULL)",
        ("Test Home Loan", "testhomeloan"),
    ).lastrowid
    conn.commit()

    _assign(conn, transaction_id, "Test Home Loan", "Test Home Loan")

    payee = conn.execute("SELECT account_id FROM payees WHERE id = ?", (payee_id,)).fetchone()
    assert payee["account_id"] == loan_id

    override = conn.execute(
        "SELECT account_id FROM transactions_overrides WHERE transaction_hash = ?", (transaction_hash,)
    ).fetchone()
    assert override is None


def test_assign_requires_category(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    transaction_hash = transaction_factory(
        checking_id, "2026-01-15", -50000, "FIRST NATIONAL HOME LOAN PAYMENT 12345"
    )
    transaction_id = conn.execute(
        "SELECT id FROM transactions WHERE transaction_hash = ?", (transaction_hash,)
    ).fetchone()["id"]

    with pytest.raises(ValueError):
        _assign(conn, transaction_id, "Test Home Loan", "")


def test_assign_links_payee_and_overrides_when_account_differs_from_default(
    conn, account_factory, transaction_factory
):
    checking_id = account_factory("Test Checking")
    loan_id = account_factory("Test Home Loan", type="liability")
    transaction_hash = transaction_factory(
        checking_id, "2026-01-15", -50000, "FIRST NATIONAL HOME LOAN PAYMENT 12345"
    )
    transaction_id = conn.execute(
        "SELECT id FROM transactions WHERE transaction_hash = ?", (transaction_hash,)
    ).fetchone()["id"]

    _assign(conn, transaction_id, "Test Home Loan", "Test Home Loan")

    txn = conn.execute("SELECT payee_id FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    payee = conn.execute("SELECT account_id FROM payees WHERE id = ?", (txn["payee_id"],)).fetchone()
    override = conn.execute(
        "SELECT account_id FROM transactions_overrides WHERE transaction_hash = ?", (transaction_hash,)
    ).fetchone()

    assert txn["payee_id"] is not None
    assert payee["account_id"] == loan_id
    assert override is None


def test_payee_default_categories_only_lists_payees_that_have_a_category(conn, account_factory):
    groceries_id = account_factory("Test Groceries", type="expense")
    conn.executemany(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) VALUES (?, ?, ?)",
        [("Test Costco", "testcostco", groceries_id), ("Test Unfiled", "testunfiled", None)],
    )
    conn.commit()

    defaults = get_payee_default_categories()

    assert defaults["Test Costco"] == "Test Groceries"
    assert "Test Unfiled" not in defaults


def test_all_account_names_covers_inactive_accounts_too(account_factory):
    account_factory("Test Retired Card", type="liability", is_active=0)

    assert "Test Retired Card" in get_all_account_names()
    assert "Test Retired Card" not in [a.name for a in get_all_accounts()]
