from intent_ledger.accounting.accounts import Filter, get_ledger_entries, parse_search
from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.payees import get_or_create_payee


def test_parse_search_empty_returns_no_text_and_no_filters():
    assert parse_search(None) == ([], [])
    assert parse_search("") == ([], [])


def test_parse_search_plain_words_are_free_text():
    text, filters = parse_search("grocery store trip")
    assert text == ["grocery", "store", "trip"]
    assert filters == []


def test_parse_search_payee_and_category_filters():
    text, filters = parse_search("payee:starbucks category=Groceries")
    assert text == []
    assert filters == [
        Filter("payee", ":", "starbucks"),
        Filter("category", "=", "Groceries"),
    ]


def test_parse_search_field_name_is_case_insensitive():
    _text, filters = parse_search("PAYEE:Starbucks")
    assert filters == [Filter("payee", ":", "Starbucks")]


def test_parse_search_amount_and_amt_alias_with_operators():
    _text, filters = parse_search("amount>50 amt<=10")
    assert filters == [
        Filter("amount", ">", 50.0),
        Filter("amount", "<=", 10.0),
    ]


def test_parse_search_invalid_amount_is_silently_dropped():
    text, filters = parse_search("amount>not-a-number rest of query")
    assert filters == []
    assert text == ["rest", "of", "query"]


def test_parse_search_date_is_parsed_as_day_first():
    _text, filters = parse_search("date:15/01/2026")
    assert filters == [Filter("date", ":", "2026-01-15")]


def test_parse_search_date_with_operator():
    _text, filters = parse_search("date>=01/03/2026")
    assert filters == [Filter("date", ">=", "2026-03-01")]


def test_parse_search_invalid_date_is_silently_dropped():
    text, filters = parse_search("date:not-a-date")
    assert filters == []
    assert text == []


def test_parse_search_mixes_free_text_and_filters():
    text, filters = parse_search("grocery payee:starbucks amount>10")
    assert text == ["grocery"]
    assert filters == [
        Filter("payee", ":", "starbucks"),
        Filter("amount", ">", 10.0),
    ]


def test_get_ledger_entries_payee_filter_is_case_insensitive_substring(
    conn, account_factory, transaction_factory
):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")

    with_payee = transaction_factory(checking_id, "2026-01-05", -1500, "TRADER JOES")
    conn.execute(
        "UPDATE transactions SET payee_id = ? WHERE transaction_hash = ?",
        (get_or_create_payee(conn, "Trader Joes", "traderjoes", groceries_id), with_payee),
    )
    transaction_factory(checking_id, "2026-01-06", -900, "SOME OTHER STORE")
    conn.commit()
    rebuild_ledger()

    rows = get_ledger_entries(checking_id, search="payee:trader")

    assert len(rows) == 1
    assert rows[0]["payee"] == "Trader Joes"


def test_get_ledger_entries_category_filter_matches_resolved_account(
    conn, account_factory, transaction_factory
):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    dining_id = account_factory("Test Dining", type="expense")
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id, priority) "
        "VALUES ('equals', 'GROCERY RUN', ?, 0)",
        (groceries_id,),
    )
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id, priority) "
        "VALUES ('equals', 'DINNER OUT', ?, 0)",
        (dining_id,),
    )
    conn.commit()

    transaction_factory(checking_id, "2026-01-05", -1500, "GROCERY RUN")
    transaction_factory(checking_id, "2026-01-06", -3000, "DINNER OUT")
    rebuild_ledger()

    rows = get_ledger_entries(checking_id, search="category:groceries")

    assert len(rows) == 1
    assert rows[0]["category"] == "Test Groceries"


def test_get_ledger_entries_amount_filter_compares_absolute_value(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    transaction_factory(checking_id, "2026-01-05", -1500, "SMALL")
    transaction_factory(checking_id, "2026-01-06", -9000, "BIG")
    rebuild_ledger()

    rows = get_ledger_entries(checking_id, search="amount>50")

    assert len(rows) == 1
    assert rows[0]["amount"] == -90.0


def test_get_ledger_entries_date_filter_with_operator(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    transaction_factory(checking_id, "2026-01-05", -1500, "EARLY")
    transaction_factory(checking_id, "2026-02-10", -1500, "LATE")
    rebuild_ledger()

    rows = get_ledger_entries(checking_id, search="date>=01/02/2026")

    assert len(rows) == 1
    assert rows[0]["date"] == "2026-02-10"


def test_get_ledger_entries_free_text_matches_category_payee_date_or_amount(
    conn, account_factory, transaction_factory
):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id, priority) "
        "VALUES ('equals', 'GROCERY RUN', ?, 0)",
        (groceries_id,),
    )
    conn.commit()

    transaction_factory(checking_id, "2026-01-05", -1500, "GROCERY RUN")
    transaction_factory(checking_id, "2026-01-06", -3000, "SOMETHING ELSE")
    rebuild_ledger()

    rows = get_ledger_entries(checking_id, search="groceries")

    assert len(rows) == 1
    assert rows[0]["category"] == "Test Groceries"


def test_get_ledger_entries_sort_orders(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    transaction_factory(checking_id, "2026-01-05", -1000, "SMALLER")
    transaction_factory(checking_id, "2026-01-06", -3000, "BIGGER")
    rebuild_ledger()

    asc = get_ledger_entries(checking_id, sort="amount_asc")
    assert [r["amount"] for r in asc] == [-30.0, -10.0]

    desc = get_ledger_entries(checking_id, sort="amount_desc")
    assert [r["amount"] for r in desc] == [-10.0, -30.0]


def test_get_ledger_entries_limit(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    for i in range(5):
        transaction_factory(checking_id, f"2026-01-{i + 1:02d}", -1000, f"TXN {i}")
    rebuild_ledger()

    rows = get_ledger_entries(checking_id, limit=2)

    assert len(rows) == 2
