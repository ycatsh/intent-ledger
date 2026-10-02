import calendar
import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

from intent_ledger.db import db
from intent_ledger.domain.money import Money, scaled
from intent_ledger.settings import today as current_day

PALETTE = ["#4a9eff", "#3d9970", "#c9921a", "#c0392b", "#9a9a9a", "#8e6fc9"]
ACCENT, GREEN, _, RED, *_ = PALETTE


def _series(label, data, color, dashed=False):
    return {"label": label, "data": data, "color": color, "dashed": dashed}


def expense_charts(progress, trend):
    return {
        "burn": {
            "labels": progress["labels"],
            "datasets": [
                _series("Spent", progress["spent"], RED),
                _series("Budget", progress["budget"], ACCENT, dashed=True),
            ],
        },
        "income": {"labels": trend["labels"], "datasets": [_series("Income", trend["income"], GREEN)]},
    }


def project_charts(trend, budget):
    cumulative = [_series("Spent", trend["cumulative_expenses"], RED)]
    if budget:
        cumulative.append(_series("Budget", [budget] * len(trend["labels"]), ACCENT, dashed=True))
    if trend["has_income"]:
        cumulative.append(_series("Earned", trend["cumulative_income"], GREEN))

    return {
        "cumulative": {"labels": trend["labels"], "datasets": cumulative},
        "daily": {"labels": trend["labels"], "datasets": [_series("Spent", trend["daily_expenses"], RED)]},
    }


def get_charts_page():
    with db.transaction() as conn:
        return {
            "net_worth": _net_worth(conn),
            "cashflow": _cashflow(conn),
            "spend_pace": _spend_pace(conn),
            "spend_flexibility": _spend_flexibility(conn),
            "category_pace": _category_pace(conn),
            "account_balances": _account_balances(conn),
            "day_of_week": _day_of_week(conn),
            "transaction_sizes": _transaction_size_histogram(conn),
        }


def _net_worth(conn, months=12):
    _, keys, balances = _balances_by_month(conn, months)
    totals = [sum(series[key] for series in balances.values()) for key in keys]
    return {
        "labels": keys,
        "datasets": [{"label": "Net Worth", "data": [Money(t).amount for t in totals], "color": "#3d9970"}],
    }


def cashflow_chart(months=6):
    with db.transaction() as conn:
        return _cashflow(conn, months)


def _cashflow(conn, months=6):
    keys = _last_n_month_keys(months)
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m', period) AS ym,
               SUM(CASE WHEN account_type = 'income' THEN amount_cents ELSE -amount_cents END) AS net
        FROM income_expense_lines
        WHERE period >= ?
        GROUP BY period
        """,
        (f"{keys[0]}-01",),
    ).fetchall()

    by_month = {row["ym"]: row["net"] for row in rows}
    net = [Money(by_month.get(key, 0)).amount for key in keys]

    return {
        "labels": keys,
        "datasets": [
            {"label": "Saved", "data": [v if v >= 0 else None for v in net], "color": "#3d9970"},
            {"label": "Overspent", "data": [v if v < 0 else None for v in net], "color": "#c0392b"},
        ],
    }


def _daily_spending(conn, start, end):
    rows = conn.execute(
        """
        SELECT posted_date AS day, SUM(amount_cents) AS amount_cents
        FROM income_expense_lines
        WHERE account_type = 'expense' AND posted_date BETWEEN ? AND ?
        GROUP BY posted_date
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return {row["day"]: row["amount_cents"] for row in rows}


def _spend_pace(conn):
    today = current_day()
    this_month_start = today.replace(day=1)
    last_month_end = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    def daily_cumulative(start, end, until=None):
        by_day = _daily_spending(conn, start, end)
        days = calendar.monthrange(start.year, start.month)[1]
        cumulative = []
        total = 0
        for day in range(1, days + 1):
            d = start.replace(day=day)
            if until is not None and d > until:
                cumulative.append(None)
                continue
            total += by_day.get(d.isoformat(), 0)
            cumulative.append(Money(total).amount)
        return cumulative

    this_month = daily_cumulative(this_month_start, today, until=today)
    last_month = daily_cumulative(last_month_start, last_month_end)

    window_start = date.fromisoformat(f"{_last_n_month_keys(4)[0]}-01")
    window_days = (this_month_start - window_start).days
    window_cents = sum(_daily_spending(conn, window_start, last_month_end).values())
    avg_line = [Money(scaled(window_cents, i + 1, window_days)).amount for i in range(len(this_month))]

    days_this_month = calendar.monthrange(today.year, today.month)[1]
    labels = [str(i) for i in range(1, days_this_month + 1)]

    return {
        "labels": labels,
        "datasets": [
            {"label": "This month", "data": this_month, "color": "#4a9eff"},
            {"label": "Last month", "data": last_month[: len(this_month)], "color": "#9a9a9a"},
            {"label": "3mo avg pace", "data": avg_line, "color": "#c9921a"},
        ],
    }


def _spend_flexibility(conn, months=12):
    month_keys = _last_n_month_keys(months)

    recurring_rows = conn.execute(
        """
        SELECT strftime('%Y-%m', posted_date) ym, SUM(amount_cents) total
        FROM subscriptions_charges
        WHERE posted_date >= date(?, ?)
        GROUP BY ym
    """,
        (current_day().isoformat(), f"-{months} months"),
    ).fetchall()

    total_rows = conn.execute(
        """
        SELECT strftime('%Y-%m', period) AS ym, SUM(amount_cents) AS total
        FROM income_expense_lines
        WHERE account_type = 'expense' AND period >= ?
        GROUP BY period
        """,
        (f"{month_keys[0]}-01",),
    ).fetchall()

    recurring = dict.fromkeys(month_keys, 0)
    total = dict.fromkeys(month_keys, 0)

    for r in recurring_rows:
        if r["ym"] in recurring:
            recurring[r["ym"]] = -r["total"]
    for r in total_rows:
        if r["ym"] in total:
            total[r["ym"]] = r["total"]

    fixed_pct = []
    flexible_pct = []
    for k in month_keys:
        spend = max(total[k], recurring[k])
        pct = (recurring[k] / spend * 100) if spend else 0
        fixed_pct.append(round(pct, 1))
        flexible_pct.append(round(100 - pct, 1))

    return {
        "labels": month_keys,
        "datasets": [
            {"label": "Fixed %", "data": fixed_pct, "color": "#9a9a9a"},
            {"label": "Flexible %", "data": flexible_pct, "color": "#4a9eff"},
        ],
    }


def _category_pace(conn, baseline_months=6, min_abs_z=1.0, max_items=8):
    today = current_day()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    day_frac = today.day / days_in_month

    categories = conn.execute("""
        SELECT id, name FROM accounts WHERE type = 'expense' AND budget = 1 AND is_active = 1
    """).fetchall()

    if not categories:
        return {"labels": [], "datasets": []}

    baseline_keys = _last_n_month_keys(baseline_months + 1)[:-1]

    baseline_rows = conn.execute(
        """
        SELECT account_id AS id, strftime('%Y-%m', period) AS ym, SUM(amount_cents) AS total
        FROM income_expense_lines
        WHERE account_type = 'expense' AND account_budget = 1 AND period >= ? AND period < ?
        GROUP BY account_id, period
        """,
        (f"{baseline_keys[0]}-01", today.replace(day=1).isoformat()),
    ).fetchall()

    current_rows = conn.execute(
        """
        SELECT account_id AS id, SUM(amount_cents) AS total
        FROM income_expense_lines
        WHERE account_type = 'expense' AND account_budget = 1 AND posted_date BETWEEN ? AND ?
        GROUP BY account_id
        """,
        (today.replace(day=1).isoformat(), today.isoformat()),
    ).fetchall()

    by_account = {}
    for r in baseline_rows:
        by_account.setdefault(r["id"], {})[r["ym"]] = Money(r["total"]).amount

    current_by_account = {r["id"]: Money(r["total"]).amount for r in current_rows}

    results = []
    for cat in categories:
        baseline_vals = [by_account.get(cat["id"], {}).get(k, 0) for k in baseline_keys]
        mean_v = statistics.mean(baseline_vals)
        stdev_v = statistics.pstdev(baseline_vals)

        expected = mean_v * day_frac
        stdev_scaled = max(stdev_v * math.sqrt(day_frac), 5.0)
        actual = current_by_account.get(cat["id"], 0)
        delta = actual - expected
        z = delta / stdev_scaled

        results.append((cat["name"], delta, z))

    flagged = [r for r in results if abs(r[2]) >= min_abs_z]
    if len(flagged) < 6:
        flagged = sorted(results, key=lambda r: abs(r[2]), reverse=True)[:6]
    else:
        flagged = sorted(flagged, key=lambda r: abs(r[2]), reverse=True)[:max_items]

    flagged.sort(key=lambda r: r[1], reverse=True)

    labels = [r[0] for r in flagged]
    over = [round(r[1], 2) if r[1] > 0 else None for r in flagged]
    under = [round(r[1], 2) if r[1] <= 0 else None for r in flagged]

    return {
        "labels": labels,
        "datasets": [
            {"label": "Over pace", "data": over, "color": "#c0392b"},
            {"label": "Under pace", "data": under, "color": "#3d9970"},
        ],
    }


def _day_of_week(conn, days=90):
    rows = conn.execute(
        """
        SELECT CAST(strftime('%w', posted_date) AS INTEGER) AS dow, SUM(amount_cents) AS total
        FROM income_expense_lines
        WHERE account_type = 'expense' AND posted_date >= date(?, ?)
        GROUP BY dow
        """,
        (current_day().isoformat(), f"-{days} days"),
    ).fetchall()

    by_dow = {r["dow"]: Money(r["total"]).amount for r in rows}
    labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    return {
        "labels": labels,
        "datasets": [
            {"label": "Spend", "data": [round(by_dow.get(i, 0), 2) for i in range(7)], "color": "#4a9eff"}
        ],
    }


def _account_balances(conn, months=12):
    accounts, keys, balances = _balances_by_month(conn, months)
    datasets = [
        {"label": account["name"], "data": [Money(balances[account["id"]][key]).amount for key in keys]}
        for account in accounts
        if any(balances[account["id"]].values())
    ]

    datasets.sort(key=lambda d: abs(d["data"][-1]), reverse=True)
    datasets = datasets[:3]
    for i, d in enumerate(datasets):
        d["color"] = PALETTE[i % len(PALETTE)]

    return {"labels": keys, "datasets": datasets}


def _balances_by_month(conn, months):
    keys = _last_n_month_keys(months)
    month_ends = [date(int(key[:4]), int(key[5:]), 1) for key in keys]
    month_ends = [d.replace(day=calendar.monthrange(d.year, d.month)[1]).isoformat() for d in month_ends]

    accounts = conn.execute(
        "SELECT id, name FROM accounts WHERE type IN ('asset', 'liability') ORDER BY id"
    ).fetchall()
    points = defaultdict(list)
    for row in conn.execute(
        """
        SELECT s.account_id, s.snapshot_date, s.balance_cents
        FROM account_snapshots s
        JOIN accounts a ON a.id = s.account_id
        WHERE a.type IN ('asset', 'liability')
        ORDER BY s.account_id, s.snapshot_date
        """
    ):
        points[row["account_id"]].append((row["snapshot_date"], row["balance_cents"]))

    balances = {}
    for account in accounts:
        series, balance, index = {}, 0, 0
        history = points[account["id"]]
        for key, month_end in zip(keys, month_ends, strict=True):
            while index < len(history) and history[index][0] <= month_end:
                balance = history[index][1]
                index += 1
            series[key] = balance
        balances[account["id"]] = series

    return accounts, keys, balances


def _transaction_size_histogram(conn, days=180):
    rows = conn.execute(
        """
        SELECT amount_cents AS amt
        FROM income_expense_lines
        WHERE account_type = 'expense' AND amount_cents > 0 AND posted_date >= date(?, ?)
        """,
        (current_day().isoformat(), f"-{days} days"),
    ).fetchall()

    buckets = [0, 10, 25, 50, 100, 250, 500, 1000, 10000, float("inf")]

    labels = [
        f"<{buckets[1]}",
        *[f"{int(buckets[i])}-{int(buckets[i + 1])}" for i in range(1, len(buckets) - 2)],
        f"{int(buckets[-2])}+",
    ]

    counts = [0] * (len(labels))

    for r in rows:
        amt = Money(r["amt"]).amount

        for i in range(len(buckets) - 1):
            if buckets[i] <= amt < buckets[i + 1]:
                counts[i] += 1
                break

    dataset = {"label": "Transactions", "data": counts, "color": ACCENT, "unit": "count"}
    return {"labels": labels, "datasets": [dataset]}


def _last_n_month_keys(n):
    today = current_day()
    keys = []
    y, m = today.year, today.month
    for _ in range(n):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(keys))
