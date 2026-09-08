import pytest

from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.rules import preview_rule_matches


def test_preview_rule_matches_finds_transactions_by_current_category(
    conn, account_factory, transaction_factory
):
    checking_id = account_factory("Test Checking")
    account_factory("Test Dividends", type="income")

    transaction_factory(checking_id, "2026-01-05", 5000, "ACH/HARBOR RIDGE HOLDINGS/2173672")
    transaction_factory(checking_id, "2026-01-06", 5000, "ACH/MERIDIAN CAPITAL GROUP/254216778")
    transaction_factory(checking_id, "2026-01-07", -100, "COFFEE SHOP")
    rebuild_ledger()

    matches = preview_rule_matches("prefix", "ACH/")

    assert len(matches) == 2
    descriptions = {m["raw_description"] for m in matches}
    assert descriptions == {"ACH/HARBOR RIDGE HOLDINGS/2173672", "ACH/MERIDIAN CAPITAL GROUP/254216778"}
    assert all(m["current_category"] == "Unknown" for m in matches)


def test_preview_rule_matches_case_insensitive_and_ordered_newest_first(
    conn, account_factory, transaction_factory
):
    checking_id = account_factory("Test Checking")

    transaction_factory(checking_id, "2026-01-05", -100, "coffee shop downtown")
    transaction_factory(checking_id, "2026-02-05", -100, "COFFEE SHOP UPTOWN")
    rebuild_ledger()

    matches = preview_rule_matches("contains", "coffee shop")

    assert [m["raw_description"] for m in matches] == ["COFFEE SHOP UPTOWN", "coffee shop downtown"]


def test_preview_rule_matches_raises_on_invalid_regex(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    transaction_factory(checking_id, "2026-01-05", -100, "COFFEE SHOP")
    rebuild_ledger()

    with pytest.raises(ValueError):
        preview_rule_matches("regex", "(unclosed")


def test_preview_rule_matches_raises_on_unknown_match_type(conn):
    with pytest.raises(ValueError):
        preview_rule_matches("fuzzy", "anything")
