from collections import defaultdict
from datetime import date, timedelta

from intent_ledger.db import db
from intent_ledger.domain.money import Money
from intent_ledger.settings import today


def percent_of_total(rows, amount_key="amount"):
    """Return each row's share of the total (1 decimal, 0 if total is zero)."""
    total = sum(r[amount_key] for r in rows)
    percents = [round(100 * r[amount_key] / total, 1) if total else 0 for r in rows]
    return percents


def _period_where(column, year=None, month=None, start=None, end=None):
    """WHERE-clause fragment + params: a year, or year+month if month is given."""
    if start and end:
        return f"{column} BETWEEN ? AND ?", [start, end]

    if month is None:
        return f"strftime('%Y', {column}) = ?", [f"{year:04d}"]

    return (
        f"strftime('%Y', {column}) = ? AND strftime('%m', {column}) = ?",
        [f"{year:04d}", f"{month:02d}"],
    )


def get_monthly_summary(year=None, month=None, start=None, end=None):
    with db.transaction() as conn:
        where, params = _period_where("t.posted_date", year, month, start, end)
        ledger_row = conn.execute(
            f"""
            SELECT
                -SUM(
                    CASE
                        WHEN a.type = 'income' AND l.amount_cents < 0
                        THEN l.amount_cents
                        ELSE 0
                    END
                ) / 100.0 AS income,

                SUM(
                    CASE
                        WHEN a.type = 'expense' AND l.amount_cents > 0
                        THEN l.amount_cents
                        ELSE 0
                    END
                ) / 100.0 AS expenses

            FROM ledger l
            JOIN accounts a
                ON a.id = l.account_id
            JOIN transactions t
                ON t.id = l.transaction_id
            WHERE {where}
            """,
            params,
        ).fetchone()

        where, params = _period_where("posted_date", year, month, start, end)
        txn_row = conn.execute(
            f"""
            SELECT
                SUM(
                    CASE
                        WHEN amount_cents > 0
                        THEN amount_cents
                        ELSE 0
                    END
                ) / 100.0 AS inflow,

                -SUM(
                    CASE
                        WHEN amount_cents < 0
                        THEN amount_cents
                        ELSE 0
                    END
                ) / 100.0 AS outflow,

                COUNT(*) AS transactions

            FROM transactions
            WHERE {where}
            """,
            params,
        ).fetchone()

    income = ledger_row["income"] or 0
    expenses = ledger_row["expenses"] or 0
    inflow = txn_row["inflow"] or 0
    outflow = txn_row["outflow"] or 0

    return {
        "income": income,
        "inflow": inflow,
        "expenses": expenses,
        "outflow": outflow,
        "net": income - expenses,
        "cashflow": inflow - outflow,
        "transactions": txn_row["transactions"],
    }


def get_category_summary(year=None, month=None, start=None, end=None):
    with db.transaction() as conn:
        where, params = _period_where("t.posted_date", year, month, start, end)
        rows = conn.execute(
            f"""
            SELECT
            a.name category,
            SUM(l.amount_cents)/100.0 amount,
            COUNT(*) transactions
            FROM ledger l
            JOIN accounts a
                ON a.id = l.account_id
            JOIN transactions t
                ON t.id = l.transaction_id
            WHERE a.type = 'expense'
              AND l.amount_cents > 0
              AND {where}
            GROUP BY a.id
            ORDER BY amount DESC
            """,
            params,
        ).fetchall()

    percents = percent_of_total(rows)

    return [
        {
            "category": r["category"],
            "amount": r["amount"],
            "transactions": r["transactions"],
            "percent": p,
        }
        for r, p in zip(rows, percents, strict=True)
    ]


def get_payee_summary(year=None, month=None, start=None, end=None, limit=None):
    with db.transaction() as conn:
        where, params = _period_where("t.posted_date", year, month, start, end)
        rows = conn.execute(
            f"""
            SELECT
                m.canonical_name payee,
                SUM(l.amount_cents) / 100.0 amount,
                COUNT(*) transactions
            FROM ledger l
            JOIN accounts a
                ON a.id = l.account_id
            JOIN transactions t
                ON t.id = l.transaction_id
            JOIN payees m
                ON m.id = t.payee_id
            WHERE a.type = 'expense'
            AND l.amount_cents > 0
              AND {where}
            GROUP BY m.id
            ORDER BY amount DESC
            """
            + (" LIMIT ?" if limit else ""),
            params + ([limit] if limit else []),
        ).fetchall()

    percents = percent_of_total(rows)

    return [
        {
            "payee": r["payee"],
            "amount": r["amount"],
            "transactions": r["transactions"],
            "percent": p,
        }
        for r, p in zip(rows, percents, strict=True)
    ]


def get_inflow_summary(year=None, month=None, start=None, end=None, limit=None):
    with db.transaction() as conn:
        where, params = _period_where("t.posted_date", year, month, start, end)
        rows = conn.execute(
            f"""
            SELECT
                m.canonical_name source,
                SUM(t.amount_cents) / 100.0 amount,
                COUNT(*) transactions
            FROM transactions t
            JOIN payees m
                ON m.id = t.payee_id
            WHERE t.amount_cents > 0
              AND {where}
            GROUP BY m.id
            ORDER BY amount DESC
            """
            + (" LIMIT ?" if limit else ""),
            params + ([limit] if limit else []),
        ).fetchall()

    percents = percent_of_total(rows)

    return [
        {
            "source": r["source"],
            "amount": r["amount"],
            "transactions": r["transactions"],
            "percent": p,
        }
        for r, p in zip(rows, percents, strict=True)
    ]


def monthly_income(limit=None):
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                period,
                income
            FROM monthly_income
            ORDER BY period DESC
            LIMIT ?
        """,
            (limit,),
        ).fetchall()

    rows.reverse()

    return {
        "labels": [r["period"][:7] for r in rows],
        "income": [Money(-r["income"]).amount for r in rows],
    }


def budget_progress(start: str, end: str):
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    days = (end_date - start_date).days + 1

    with db.transaction() as conn:
        budget = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents),0) AS budget
            FROM budgets
            WHERE period >= date(?, 'start of month')
              AND period <= date(?, 'start of month')
        """,
            (start, end),
        ).fetchone()["budget"]

        rows = conn.execute(
            """
            SELECT
                t.posted_date AS day,
                -SUM(l.amount_cents) AS spent
            FROM ledger l
            JOIN accounts a
                ON a.id=l.account_id
            JOIN transactions t
                ON t.id=l.transaction_id
            WHERE a.type='expense'
              AND l.amount_cents > 0
              AND t.posted_date BETWEEN ? AND ?
            GROUP BY day
            ORDER BY day
        """,
            (start, end),
        ).fetchall()

    daily = {r["day"]: Money(r["spent"]).amount for r in rows}

    today_ = today()

    labels = []
    spent = []
    budget_line = []

    cumulative = 0
    d = start_date
    day_index = 0
    budget_amount = Money(budget).amount

    while d <= end_date:
        iso = d.isoformat()
        day_index += 1
        cumulative -= daily.get(iso, 0)

        labels.append(iso)
        spent.append(cumulative if d <= today_ else None)
        budget_line.append(budget_amount * day_index / days)

        d += timedelta(days=1)

    return {
        "labels": labels,
        "spent": spent,
        "budget": budget_line,
    }


def get_recurring_payments(min_occurrences=3):
    rows = _fetch_transactions_with_date()
    groups = defaultdict(list)

    for row in rows:
        groups[(row["payee_id"], row["amount_cents"])].append(row)

    today_ = today()
    recurring = []

    for (_, amount), txns in groups.items():
        if len(txns) < min_occurrences:
            continue

        txns.sort(key=lambda x: x["posted_date"])
        dates = [date.fromisoformat(txn["posted_date"]) for txn in txns]
        intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]

        if max(intervals) - min(intervals) > 5:
            continue

        avg_interval = round(sum(intervals) / len(intervals))

        if 27 <= avg_interval <= 32:
            frequency = "Monthly"
        elif 350 <= avg_interval <= 380:
            frequency = "Yearly"
        else:
            continue

        last_payment = dates[-1]

        if (today_ - last_payment).days > 90:
            continue

        next_payment = last_payment + timedelta(days=avg_interval)

        if today_ > next_payment + timedelta(days=30):
            next_payment = "Canceled"

        recurring.append(
            {
                "payee_id": txns[0]["payee_id"],
                "payee": txns[0]["canonical_name"],
                "frequency": frequency,
                "amount": Money(amount).amount,
                "occurrences": len(txns),
                "last_payment": last_payment,
                "next_payment": next_payment,
            }
        )

    return sorted(recurring, key=lambda x: x["next_payment"] == "Canceled")


def _fetch_transactions_with_date():
    with db.transaction() as conn:
        return conn.execute(
            """
            SELECT
                t.posted_date,
                t.amount_cents,
                t.payee_id,
                m.canonical_name
            FROM transactions t
            JOIN payees m
                ON m.id = t.payee_id
            ORDER BY t.payee_id, t.posted_date
            """
        ).fetchall()
