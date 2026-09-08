import pytest

from intent_ledger.accounting.mappings import (
    ALLOWED_FIELDS,
    assert_allowed_fields_match_schema,
    save_mappings,
)
from intent_ledger.accounting.payees import get_or_create_payee
from intent_ledger.accounting.repositories.accounts import AccountRepository


def test_allowed_fields_match_live_schema(conn):
    assert_allowed_fields_match_schema(conn)


def test_allowed_fields_catches_drift(conn):
    ALLOWED_FIELDS["accounts"].add("not_a_real_column")

    try:
        with pytest.raises(RuntimeError):
            assert_allowed_fields_match_schema(conn)
    finally:
        ALLOWED_FIELDS["accounts"].discard("not_a_real_column")


def test_get_or_create_payee_matches_by_normalized_name(conn, account_factory):
    account_id = account_factory("Test Groceries", type="expense")

    first_id = get_or_create_payee(conn, "Coffee Shop", "coffeeshop", account_id)
    second_id = get_or_create_payee(conn, "COFFEE  SHOP!!", "coffeeshop", account_id)

    assert first_id == second_id


def test_account_names_are_unique_case_insensitively(conn, account_factory):
    account_factory("Test Groceries", type="expense")

    with pytest.raises(ValueError):
        save_mappings(
            [{"op": "insert", "table": "accounts", "temp_id": "t1", "fields": {"name": "test groceries"}}]
        )


def test_renaming_an_account_to_an_existing_name_is_rejected(conn, account_factory):
    account_factory("Test Groceries", type="expense")
    dining_id = account_factory("Test Dining", type="expense")

    with pytest.raises(ValueError):
        save_mappings(
            [{"op": "update", "table": "accounts", "id": dining_id, "fields": {"name": "TEST GROCERIES"}}]
        )


def test_get_or_create_expense_reuses_an_existing_account_by_case(conn):
    first_id = AccountRepository(conn).get_or_create_expense("Test Groceries")
    second_id = AccountRepository(conn).get_or_create_expense("TEST GROCERIES")

    assert first_id == second_id


def test_account_cannot_be_nested_under_a_different_type_parent(conn, account_factory):
    checking_id = account_factory("Test Checking", type="asset")

    with pytest.raises(ValueError, match="can't be nested"):
        save_mappings(
            [
                {
                    "op": "insert",
                    "table": "accounts",
                    "temp_id": "t1",
                    "fields": {"name": "Rent", "type": "expense", "parent_account_id": checking_id},
                }
            ]
        )


def test_account_can_be_nested_under_a_same_type_parent(conn, account_factory):
    housing_id = account_factory("Test Housing", type="expense")

    save_mappings(
        [
            {
                "op": "insert",
                "table": "accounts",
                "temp_id": "t1",
                "fields": {"name": "Rent", "type": "expense", "parent_account_id": housing_id},
            }
        ]
    )

    row = conn.execute("SELECT parent_account_id FROM accounts WHERE name = 'Rent'").fetchone()
    assert row["parent_account_id"] == housing_id


def test_changing_parent_to_a_different_type_is_rejected(conn, account_factory):
    housing_id = account_factory("Test Housing", type="expense")
    rent_id = account_factory("Rent", type="expense", parent_account_id=housing_id)
    checking_id = account_factory("Test Checking", type="asset")

    with pytest.raises(ValueError, match="can't be nested"):
        save_mappings(
            [
                {
                    "op": "update",
                    "table": "accounts",
                    "id": rent_id,
                    "fields": {"parent_account_id": checking_id},
                }
            ]
        )
