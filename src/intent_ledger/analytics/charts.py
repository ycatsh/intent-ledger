import calendar
import math
import statistics
from datetime import date, timedelta

from intent_ledger.db import db
from intent_ledger.domain.money import Money
from intent_ledger.settings import today_in

PALETTE = ["#4a9eff", "#3d9970", "#c9921a", "#c0392b", "#9a9a9a", "#8e6fc9"]


def bar_chart(rows, label_field, value_field, color):
    return {
        "labels": [r[label_field] for r in rows],
        "datasets": [
            {
                "label": value_field.capitalize(),
                "data": [round(abs(r[value_field]), 2) for r in rows],
                "color": color,
            }
        ],
    }


def line_chart(labels, series):
    """series: an iterable of (label, data, color) tuples."""
    return {
        "labels": labels,
        "datasets": [{"label": label, "data": data, "color": color} for label, data, color in series],
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
        }


def _net_worth(conn, months=12):
    today = today_in(conn)
    month_end = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    ym = f"{today.year:04d}-{today.month:02d}"

    rows = conn.execute(
        """
        WITH RECURSIVE months(month_end, ym, n) AS (
            SELECT
                ?,
                ?,
                1
            UNION ALL
            SELECT
                date(month_end,'start of month','-1 day'),
                strftime('%Y-%m',date(month_end,'start of month','-1 day')),
                n + 1
            FROM months
            WHERE n < ?
        )
        SELECT
            m.ym,
            SUM(s.balance_cents) AS total
        FROM months m
        JOIN accounts a
          ON a.type IN ('asset','liability')
        LEFT JOIN account_snapshots s
          ON s.account_id = a.id
         AND s.snapshot_date = (
                SELECT MAX(snapshot_date)
                FROM account_snapshots
                WHERE account_id = a.id
                  AND snapshot_date <= m.month_end
            )
        GROUP BY m.ym
        ORDER BY m.ym
    """,
        (month_end.isoformat(), ym, months),
    ).fetchall()

    return {
        "labels": [r["ym"] for r in rows],
        "datasets": [
            {
                "label": "Net Worth",
                "data": [round(Money(r["total"] or 0).amount, 2) for r in rows],
                "color": "#3d9970",
            }
        ],
    }


def cashflow_chart(months=6):
    with db.transaction() as conn:
        return _cashflow(conn, months=months)


def _cashflow(conn, months=6):
    latest = _latest_data_date(conn)
    month_keys = _last_n_month_keys(months, end_date=latest)

    rows = conn.execute(
        """
        SELECT
            strftime('%Y-%m', t.posted_date) ym,
            SUM(CASE WHEN a.type='income'  AND l.amount_cents < 0 THEN l.amount_cents ELSE 0 END) income,
            SUM(CASE WHEN a.type='expense' AND l.amount_cents > 0 THEN l.amount_cents ELSE 0 END) expense
        FROM ledger l
        JOIN transactions t ON t.id = l.transaction_id
        JOIN accounts a ON a.id = l.account_id
        WHERE strftime('%Y-%m', t.posted_date) >= ?
        GROUP BY ym
    """,
        (month_keys[0],),
    ).fetchall()

    by_month = {r["ym"]: r for r in rows}
    income = [
        round(Money(-(by_month[k]["income"] or 0)).amount, 2) if k in by_month else 0.0 for k in month_keys
    ]
    expense = [
        round(Money(by_month[k]["expense"] or 0).amount, 2) if k in by_month else 0.0 for k in month_keys
    ]
    net = [round(i - e, 2) for i, e in zip(income, expense, strict=True)]

    return {
        "labels": month_keys,
        "datasets": [
            {"label": "Income", "data": income, "color": "#3d9970"},
            {"label": "Expense", "data": expense, "color": "#c0392b"},
            {"label": "Net", "data": net, "color": "#4a9eff"},
        ],
    }


def _spend_pace(conn):
    """Anchored to the latest available transaction date rather than the
    real today, for the same reason as _cashflow above.
    """
    today = _latest_data_date(conn)
    this_month_start = today.replace(day=1)
    last_month_end = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    def daily_cumulative(start, end, until=None):
        rows = conn.execute(
            """
            SELECT t.posted_date d, SUM(l.amount_cents) amt
            FROM ledger l
            JOIN transactions t ON t.id = l.transaction_id
            JOIN accounts a ON a.id = l.account_id
            WHERE a.type = 'expense' AND l.amount_cents > 0 AND t.posted_date BETWEEN ? AND ?
            GROUP BY t.posted_date
            ORDER BY t.posted_date
        """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

        by_day = {r["d"]: r["amt"] for r in rows}
        days = calendar.monthrange(start.year, start.month)[1]
        cumulative = []
        total = 0
        for day in range(1, days + 1):
            d = start.replace(day=day)
            if until is not None and d > until:
                cumulative.append(None)
                continue
            total += by_day.get(d.isoformat(), 0)
            cumulative.append(round(Money(total).amount, 2))

        return cumulative

    this_month = daily_cumulative(this_month_start, today, until=today)
    last_month = daily_cumulative(last_month_start, last_month_end)

    avg3_start = (this_month_start - timedelta(days=90)).replace(day=1)
    rows = conn.execute(
        """
        SELECT t.posted_date d, SUM(l.amount_cents) amt
        FROM ledger l
        JOIN transactions t ON t.id = l.transaction_id
        JOIN accounts a ON a.id = l.account_id
        WHERE a.type = 'expense' AND l.amount_cents > 0 AND t.posted_date >= ? AND t.posted_date < ?
        GROUP BY t.posted_date
    """,
        (avg3_start.isoformat(), this_month_start.isoformat()),
    ).fetchall()

    daily_avg = (Money(sum(r["amt"] for r in rows)).amount / 90) if rows else 0
    avg_line = [round(daily_avg * (i + 1), 2) for i in range(len(this_month))]

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
    month_keys = _last_n_month_keys(months, today_in(conn))
    window_start = f"{month_keys[0]}-01"

    recurring_rows = conn.execute(
        """
        SELECT strftime('%Y-%m', posted_date) ym, SUM(amount_cents) total
        FROM subscriptions_charges
        WHERE posted_date >= ?
        GROUP BY ym
    """,
        (window_start,),
    ).fetchall()

    total_rows = conn.execute(
        """
        SELECT strftime('%Y-%m', t.posted_date) ym, SUM(l.amount_cents) total
        FROM ledger l
        JOIN transactions t ON t.id = l.transaction_id
        JOIN accounts a ON a.id = l.account_id
        WHERE a.type = 'expense' AND l.amount_cents > 0 AND t.posted_date >= ?
        GROUP BY ym
    """,
        (window_start,),
    ).fetchall()

    recurring = dict.fromkeys(month_keys, 0)
    total = dict.fromkeys(month_keys, 0)

    for r in recurring_rows:
        if r["ym"] in recurring:
            recurring[r["ym"]] = r["total"]
    for r in total_rows:
        if r["ym"] in total:
            total[r["ym"]] = r["total"]

    return {
        "labels": month_keys,
        "datasets": [
            {
                "label": "Recurring",
                "data": [round(Money(recurring[k]).amount, 2) for k in month_keys],
                "color": "#9a9a9a",
            },
            {
                "label": "Discretionary",
                "data": [round(Money(max(total[k] - recurring[k], 0)).amount, 2) for k in month_keys],
                "color": "#4a9eff",
            },
        ],
    }


def _category_pace(conn, baseline_months=6, min_abs_z=1.0, max_items=8):
    today = today_in(conn)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    day_frac = today.day / days_in_month

    categories = conn.execute("""
        SELECT id, name FROM accounts WHERE type = 'expense' AND budget = 1 AND is_active = 1
    """).fetchall()

    if not categories:
        return {"labels": [], "datasets": []}

    baseline_keys = _last_n_month_keys(baseline_months + 1, today)[:-1]
    current_month_start = today.replace(day=1)
    baseline_start = date.fromisoformat(f"{baseline_keys[0]}-01")

    baseline_rows = conn.execute(
        """
        SELECT a.id, strftime('%Y-%m', t.posted_date) ym, SUM(l.amount_cents) total
        FROM ledger l
        JOIN transactions t ON t.id = l.transaction_id
        JOIN accounts a ON a.id = l.account_id
        WHERE a.type = 'expense' AND a.budget = 1 AND l.amount_cents > 0
          AND t.posted_date >= ?
          AND t.posted_date <  ?
        GROUP BY a.id, ym
    """,
        (baseline_start.isoformat(), current_month_start.isoformat()),
    ).fetchall()

    current_rows = conn.execute(
        """
        SELECT a.id, SUM(l.amount_cents) total
        FROM ledger l
        JOIN transactions t ON t.id = l.transaction_id
        JOIN accounts a ON a.id = l.account_id
        WHERE a.type = 'expense' AND a.budget = 1 AND l.amount_cents > 0
          AND t.posted_date >= ?
          AND t.posted_date <= ?
        GROUP BY a.id
    """,
        (current_month_start.isoformat(), today.isoformat()),
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


def _account_balances(conn, months=12, top_n=3):
    accounts = conn.execute("""
        SELECT id, name
        FROM accounts
        WHERE type IN ('asset','liability')
          AND is_active=1
    """).fetchall()

    month_keys = _last_n_month_keys(months, today_in(conn))

    if not accounts:
        return {"labels": month_keys, "datasets": []}

    account_ids = [acc["id"] for acc in accounts]
    placeholders = ",".join("?" * len(account_ids))

    rows = conn.execute(
        f"""
        SELECT
            account_id,
            strftime('%Y-%m', snapshot_date) AS ym,
            balance_cents
        FROM account_snapshots
        WHERE account_id IN ({placeholders})
          AND snapshot_date >= ?
        ORDER BY account_id, snapshot_date
    """,
        (*account_ids, f"{month_keys[0]}-01"),
    ).fetchall()

    by_account = {}
    for r in rows:
        by_account.setdefault(r["account_id"], {})[r["ym"]] = Money(r["balance_cents"]).amount

    series = []
    for acc in accounts:
        by_month = by_account.get(acc["id"])
        if not by_month:
            continue

        last_known = 0
        values = []

        for k in month_keys:
            if k in by_month:
                last_known = by_month[k]
            values.append(round(last_known, 2))

        series.append({"label": acc["name"], "data": values})

    series.sort(key=lambda s: abs(s["data"][-1]) if s["data"] else 0, reverse=True)

    datasets = [{**s, "color": PALETTE[i % len(PALETTE)]} for i, s in enumerate(series[:top_n])]

    return {
        "labels": month_keys,
        "datasets": datasets,
    }


def _last_n_month_keys(n, end_date):
    keys = []
    y, m = end_date.year, end_date.month
    for _ in range(n):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(keys))


def _latest_data_date(conn) -> date:
    row = conn.execute("SELECT MAX(posted_date) AS d FROM transactions").fetchone()
    if row and row["d"]:
        return date.fromisoformat(row["d"])
    return today_in(conn)
