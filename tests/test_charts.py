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


def test_cashflow_shows_what_each_recent_month_saved_or_overspent(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    salary_id = account_factory("Test Salary", type="income")
    groceries_id = account_factory("Test Groceries", type="expense")
    _route_via_rule(conn, salary_id, "SALARY")
    _route_via_rule(conn, groceries_id, "GROCERIES")

    last_month = _months_ago(1).isoformat()
    transaction_factory(checking_id, last_month, 100000, "SALARY")
    transaction_factory(checking_id, last_month, -5000, "GROCERIES")
    transaction_factory(checking_id, _today().isoformat(), -5000, "GROCERIES")
    rebuild_ledger()

    result = _cashflow(conn, months=6)
    saved, overspent = (d["data"] for d in result["datasets"])

    assert result["labels"] == [_months_ago(n).strftime("%Y-%m") for n in range(5, -1, -1)]
    assert saved == [0.0, 0.0, 0.0, 0.0, 950.0, None]
    assert overspent == [None, None, None, None, None, -50.0]


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

    result = _account_balances(conn, months=1)

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

    result = _account_balances(conn, months=1)

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


def test_spend_flexibility_shows_the_fixed_and_flexible_share(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    subs_id = account_factory("Test Subscriptions", type="expense")
    payee_id = _netflix_subscription(conn, subs_id)

    month_start = _today().replace(day=1)
    sub_hash = transaction_factory(checking_id, month_start.isoformat(), -1500, "NETFLIX")
    transaction_factory(checking_id, month_start.isoformat(), -4000, "GROCERY STORE")
    conn.execute("UPDATE transactions SET payee_id = ? WHERE transaction_hash = ?", (payee_id, sub_hash))
    rebuild_ledger()

    result = _spend_flexibility(conn, months=1)

    assert result["labels"] == [_today().strftime("%Y-%m")]
    assert [(d["label"], d["data"]) for d in result["datasets"]] == [
        ("Fixed %", [27.3]),
        ("Flexible %", [72.7]),
    ]


def test_spend_flexibility_counts_a_charge_only_once_the_ledger_has_it(
    conn, account_factory, transaction_factory
):
    checking_id = account_factory("Test Checking")
    subs_id = account_factory("Test Subscriptions", type="expense")
    payee_id = _netflix_subscription(conn, subs_id)

    sub_hash = transaction_factory(checking_id, _today().replace(day=1).isoformat(), -1500, "NETFLIX")
    conn.execute("UPDATE transactions SET payee_id = ? WHERE transaction_hash = ?", (payee_id, sub_hash))

    result = _spend_flexibility(conn, months=1)

    assert [d["data"] for d in result["datasets"]] == [[0], [100]]


def _netflix_subscription(conn, subs_id):
    payee_id = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) VALUES ('Netflix', 'netflix', ?)",
        (subs_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO subscriptions (payee_id, account_id, name, amount_cents, cadence, first_seen_date) "
        "VALUES (?, ?, 'Netflix', -1500, 'monthly', ?)",
        (payee_id, subs_id, _today().replace(day=1).isoformat()),
    )
    return payee_id
