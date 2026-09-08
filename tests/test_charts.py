from datetime import UTC, date, datetime

from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.analytics.charts import _account_balances, _cashflow, _category_pace, _spend_flexibility


def _today() -> date:
    """UTC, matching today_in(conn)'s default timezone for a fresh test db -
    date.today() uses the local system clock, which can be a different
    calendar day than UTC and silently desync these tests from what the app
    itself considers "today".
    """
    return datetime.now(UTC).date()


def _months_ago(n: int, from_date: date | None = None) -> date:
    ref = from_date or _today()
    total = ref.year * 12 + (ref.month - 1) - n
    return date(total // 12, total % 12 + 1, 1)


def test_cashflow_anchors_to_latest_transaction_not_today(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    income_id = account_factory("Test Salary", type="income")

    stale_date = date(2020, 3, 15)
    transaction_factory(checking_id, stale_date.isoformat(), 100000, "Salary")
    transaction_factory(income_id, stale_date.isoformat(), -100000, "Salary")
    rebuild_ledger()

    result = _cashflow(conn, months=6)

    assert result["labels"][-1] == "2020-03"
    assert len(result["labels"]) == 6

    income_dataset = next(d for d in result["datasets"] if d["label"] == "Income")
    assert income_dataset["data"][-1] == 1000.0
    # Earlier months in the window with no data should be zero, not missing.
    assert income_dataset["data"][0] == 0.0


def test_cashflow_zero_fills_months_with_no_activity(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    expense_id = account_factory("Test Groceries", type="expense")

    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id, priority) "
        "VALUES ('contains', 'GROCERIES', ?, 0)",
        (expense_id,),
    )
    conn.commit()

    d1 = date(2021, 1, 10)
    d2 = date(2021, 3, 10)
    transaction_factory(checking_id, d1.isoformat(), -5000, "Groceries")
    transaction_factory(checking_id, d2.isoformat(), -5000, "Groceries")
    rebuild_ledger()

    result = _cashflow(conn, months=6)

    assert result["labels"] == ["2020-10", "2020-11", "2020-12", "2021-01", "2021-02", "2021-03"]
    expense_dataset = next(d for d in result["datasets"] if d["label"] == "Expense")
    assert expense_dataset["data"] == [0.0, 0.0, 0.0, 50.0, 0.0, 50.0]


def test_account_balances_returns_only_top_3_by_magnitude(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    savings_id = account_factory("Test Savings")
    cash_id = account_factory("Test Cash")
    equity_id = account_factory("Test Opening Balances", type="equity")

    today = _today().isoformat()
    transaction_factory(checking_id, today, 500000, "Open")
    transaction_factory(equity_id, today, -500000, "Open")
    transaction_factory(savings_id, today, 200000, "Open")
    transaction_factory(equity_id, today, -200000, "Open")
    transaction_factory(cash_id, today, 5000, "Open")
    transaction_factory(equity_id, today, -5000, "Open")
    rebuild_ledger()

    result = _account_balances(conn, months=1, top_n=3)

    labels = [d["label"] for d in result["datasets"]]
    assert labels == ["Test Checking", "Test Savings", "Test Cash"]


def test_account_balances_limits_to_top_n(conn, account_factory, transaction_factory):
    equity_id = account_factory("Test Opening Balances", type="equity")

    today = _today().isoformat()
    for i, amount in enumerate([100000, 90000, 80000, 70000, 60000]):
        acc_id = account_factory(f"Test Account {i}")
        transaction_factory(acc_id, today, amount, "Open")
        transaction_factory(equity_id, today, -amount, "Open")
    rebuild_ledger()

    result = _account_balances(conn, months=1, top_n=3)

    assert len(result["datasets"]) == 3
    assert [d["label"] for d in result["datasets"]] == ["Test Account 0", "Test Account 1", "Test Account 2"]


def _deactivate_seeded_budget_categories(conn):
    """seed_defaults() always creates budget=1 expense categories - clear
    them out of scope so a test can construct an exact, known set.
    """
    conn.execute("UPDATE accounts SET is_active = 0 WHERE type = 'expense' AND budget = 1")
    conn.commit()


def _route_via_rule(conn, account_id, description):
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id, priority) VALUES ('equals', ?, ?, 0)",
        (description, account_id),
    )
    conn.commit()


def test_category_pace_returns_empty_when_no_budgeted_categories(conn):
    _deactivate_seeded_budget_categories(conn)
    assert _category_pace(conn) == {"labels": [], "datasets": []}


def test_category_pace_excludes_non_budgeted_expense_accounts(conn, account_factory, transaction_factory):
    _deactivate_seeded_budget_categories(conn)
    checking_id = account_factory("Test Checking")
    unbudgeted_id = account_factory("Test Unbudgeted", type="expense", budget=0)
    _route_via_rule(conn, unbudgeted_id, "BIG SPEND")

    transaction_factory(checking_id, _today().isoformat(), -100000, "BIG SPEND")
    rebuild_ledger()

    assert _category_pace(conn) == {"labels": [], "datasets": []}


def test_category_pace_flags_overspending_and_underspending_categories(
    conn, account_factory, transaction_factory
):
    _deactivate_seeded_budget_categories(conn)
    checking_id = account_factory("Test Checking")
    big_spender_id = account_factory("Test Big Spender", type="expense", budget=1)
    frugal_id = account_factory("Test Frugal", type="expense", budget=1)
    _route_via_rule(conn, big_spender_id, "BASELINE BIG")
    _route_via_rule(conn, frugal_id, "BASELINE FRUGAL")

    # Six months of stable, low-variance baseline spend for both categories.
    for n in range(1, 7):
        month = _months_ago(n)
        transaction_factory(checking_id, month.isoformat(), -10000, "BASELINE BIG")
        transaction_factory(checking_id, month.isoformat(), -20000, "BASELINE FRUGAL")

    # This month: big spender massively over its usual pace, frugal spends nothing.
    _route_via_rule(conn, big_spender_id, "BLOWOUT")
    transaction_factory(checking_id, _today().isoformat(), -500000, "BLOWOUT")
    rebuild_ledger()

    result = _category_pace(conn)

    assert set(result["labels"]) == {"Test Big Spender", "Test Frugal"}

    over_data = next(d for d in result["datasets"] if d["label"] == "Over pace")["data"]
    under_data = next(d for d in result["datasets"] if d["label"] == "Under pace")["data"]
    over = dict(zip(result["labels"], over_data, strict=True))
    under = dict(zip(result["labels"], under_data, strict=True))

    assert over["Test Big Spender"] is not None and over["Test Big Spender"] > 0
    assert under["Test Big Spender"] is None

    assert under["Test Frugal"] is not None and under["Test Frugal"] <= 0
    assert over["Test Frugal"] is None


def test_spend_flexibility_splits_recurring_from_discretionary(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    subs_id = account_factory("Test Subscriptions", type="expense")

    payee_cursor = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) VALUES ('Netflix', 'netflix', ?)",
        (subs_id,),
    )
    payee_id = payee_cursor.lastrowid
    conn.commit()

    month_start = _today().replace(day=1)
    sub_hash = transaction_factory(checking_id, month_start.isoformat(), -1500, "NETFLIX")
    transaction_factory(checking_id, month_start.isoformat(), -4000, "GROCERY STORE")
    rebuild_ledger()

    sub_txn_id = conn.execute(
        "SELECT id FROM transactions WHERE transaction_hash = ?", (sub_hash,)
    ).fetchone()["id"]

    sub_cursor = conn.execute(
        "INSERT INTO subscriptions (payee_id, account_id, name, amount_cents, cadence, first_seen_date) "
        "VALUES (?, ?, 'Netflix', 1500, 'monthly', ?)",
        (payee_id, subs_id, month_start.isoformat()),
    )
    conn.execute(
        "INSERT INTO subscriptions_charges (subscription_id, transaction_id, amount_cents, posted_date) "
        "VALUES (?, ?, 1500, ?)",
        (sub_cursor.lastrowid, sub_txn_id, month_start.isoformat()),
    )
    conn.commit()

    result = _spend_flexibility(conn, months=1)

    assert result["labels"] == [_today().strftime("%Y-%m")]
    recurring = next(d for d in result["datasets"] if d["label"] == "Recurring")
    discretionary = next(d for d in result["datasets"] if d["label"] == "Discretionary")

    assert recurring["data"] == [15.0]
    assert discretionary["data"] == [40.0]


def test_spend_flexibility_floors_discretionary_at_zero(conn, account_factory):
    """A subscriptions_charges total can, in principle, exceed the expense
    total counted for the same month (e.g. a subscription charge posted to
    a non-expense account) - discretionary must never go negative.
    """
    checking_id = account_factory("Test Checking")
    subs_id = account_factory("Test Subscriptions", type="expense")

    payee_cursor = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) VALUES ('Netflix', 'netflix', ?)",
        (subs_id,),
    )
    payee_id = payee_cursor.lastrowid
    conn.commit()

    month_start = _today().replace(day=1)

    conn.execute(
        """
        INSERT INTO transactions (
            account_id, posted_date, amount_cents, raw_description, transaction_hash
        )
        VALUES (?, ?, -1500, 'NETFLIX', 'flex-floor-test')
        """,
        (checking_id, month_start.isoformat()),
    )
    txn_id = conn.execute(
        "SELECT id FROM transactions WHERE transaction_hash = 'flex-floor-test'"
    ).fetchone()["id"]

    sub_cursor = conn.execute(
        "INSERT INTO subscriptions (payee_id, account_id, name, amount_cents, cadence, first_seen_date) "
        "VALUES (?, ?, 'Netflix', 1500, 'monthly', ?)",
        (payee_id, subs_id, month_start.isoformat()),
    )
    conn.execute(
        "INSERT INTO subscriptions_charges (subscription_id, transaction_id, amount_cents, posted_date) "
        "VALUES (?, ?, 1500, ?)",
        (sub_cursor.lastrowid, txn_id, month_start.isoformat()),
    )
    conn.commit()
    # Deliberately not rebuilding the ledger, so no expense-account total
    # exists for this month at all.

    result = _spend_flexibility(conn, months=1)

    discretionary = next(d for d in result["datasets"] if d["label"] == "Discretionary")
    assert discretionary["data"] == [0.0]
