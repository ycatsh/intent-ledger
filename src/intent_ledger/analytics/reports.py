from calendar import monthrange
from datetime import date, timedelta

from intent_ledger.db import db
from intent_ledger.domain.money import Money, scaled
from intent_ledger.settings import today as current_day

SOURCE_KINDS = {"income": "Income", "expense": "Refund", "equity": "Equity"}


def _with_amount(rows):
    for row in rows:
        row["amount"] = Money(row["amount_cents"]).amount
    return rows


def _scope(alias, year=None, month=None, start=None, end=None, project_id=None) -> tuple[str, list]:
    if project_id is not None:
        return (
            f"{alias}.transaction_hash IN "
            "(SELECT transaction_hash FROM transactions_projects WHERE project_id = ?)",
            [project_id],
        )

    if not (start and end):
        first, last = (1, 12) if month is None else (month, month)
        start = date(year, first, 1).isoformat()
        end = date(year, last, monthrange(year, last)[1]).isoformat()

    return f"{alias}.posted_date BETWEEN ? AND ?", [start, end]


def _shares(rows, limit=None):
    total = sum(row["amount_cents"] for row in rows)
    for row in _with_amount(rows):
        row["share"] = row["amount_cents"] / total if total > 0 else 0
        row["percent"] = round(100 * row["share"], 1)
    return rows[:limit] if limit else rows


def get_summary(**scope):
    with db.transaction() as conn:
        where, params = _scope("p", **scope)
        lines = {
            (row["account_type"], row["account_budget"]): row["amount_cents"]
            for row in conn.execute(
                f"""
                SELECT p.account_type, p.account_budget, SUM(p.amount_cents) AS amount_cents
                FROM income_expense_lines p
                WHERE {where}
                GROUP BY p.account_type, p.account_budget
                """,
                params,
            )
        }

        where, params = _scope("f", **scope)
        flows = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN f.amount_cents > 0 THEN f.amount_cents END), 0) AS inflow_cents,
                -COALESCE(SUM(CASE WHEN f.amount_cents < 0 THEN f.amount_cents END), 0) AS outflow_cents
            FROM money_flows f
            WHERE {where}
            """,
            params,
        ).fetchone()

        where, params = _scope("t", **scope)
        count = conn.execute(f"SELECT COUNT(*) AS n FROM transactions t WHERE {where}", params).fetchone()

    cents = {
        "income_cents": sum(c for (kind, _), c in lines.items() if kind == "income"),
        "budgeted_cents": lines.get(("expense", 1), 0),
        "unbudgeted_cents": lines.get(("expense", 0), 0),
        **flows,
    }
    cents["expenses_cents"] = cents["budgeted_cents"] + cents["unbudgeted_cents"]
    cents["net_cents"] = cents["income_cents"] - cents["expenses_cents"]
    cents["cashflow_cents"] = cents["inflow_cents"] - cents["outflow_cents"]
    amounts = {key.removesuffix("_cents"): Money(value).amount for key, value in cents.items()}
    return {**cents, **amounts, "transactions": count["n"]}


def _expenses_by(label, key, limit, scope):
    where, params = _scope("p", **scope)

    with db.transaction() as conn:
        rows = conn.execute(
            f"""
            SELECT
                {label},
                SUM(p.amount_cents) AS amount_cents,
                COUNT(DISTINCT p.transaction_id) AS transactions
            FROM income_expense_lines p
            JOIN accounts a ON a.id = p.account_id
            LEFT JOIN payees m ON m.id = p.payee_id
            WHERE p.account_type = 'expense' AND {where}
            GROUP BY {key}
            HAVING SUM(p.amount_cents) != 0
            ORDER BY amount_cents DESC, 1
            """,
            params,
        ).fetchall()

    return _shares(rows, limit)


def get_categories(limit=None, **scope):
    return _expenses_by("a.name AS category", "a.id", limit, scope)


def get_payees(limit=None, **scope):
    return _expenses_by("COALESCE(m.canonical_name, '') AS payee", "p.payee_id", limit, scope)


def get_income_sources(limit=None, **scope):
    where, params = _scope("t", **scope)

    with db.transaction() as conn:
        rows = conn.execute(
            f"""
            SELECT
                COALESCE(m.canonical_name, '') AS payee,
                a.name AS account,
                a.type AS account_type,
                a.role AS account_role,
                -SUM(l.amount_cents) AS amount_cents,
                COUNT(DISTINCT t.id) AS transactions
            FROM ledger l
            JOIN accounts a ON a.id = l.account_id
            JOIN transactions t ON t.id = l.group_id
            LEFT JOIN payees m ON m.id = t.payee_id
            WHERE a.type IN ('income', 'expense', 'equity')
              AND l.amount_cents < 0
              AND l.group_id IN (SELECT group_id FROM money_flows WHERE amount_cents > 0)
              AND {where}
            GROUP BY payee, a.id
            ORDER BY a.type != 'income', amount_cents DESC, payee
            """,
            params,
        ).fetchall()

    for row in rows:
        unknown = row["account_role"] == "unknown"
        row["kind"] = "Uncategorized" if unknown else SOURCE_KINDS[row["account_type"]]

    return _shares(rows, limit)


def get_income_expense_lines(**scope):
    where, params = _scope("p", **scope)

    with db.transaction() as conn:
        return conn.execute(
            f"""
            SELECT
                p.posted_date AS date,
                money.name AS account,
                COALESCE(m.canonical_name, '') AS payee,
                a.name AS category,
                p.account_type AS type,
                p.amount_cents,
                t.raw_description AS description,
                EXISTS (
                    SELECT 1 FROM transactions_splits s WHERE s.transaction_hash = p.transaction_hash
                ) AS is_split
            FROM income_expense_lines p
            JOIN accounts a ON a.id = p.account_id
            JOIN transactions t ON t.id = p.transaction_id
            JOIN accounts money ON money.id = t.account_id
            LEFT JOIN payees m ON m.id = p.payee_id
            WHERE {where}
            ORDER BY p.posted_date, p.transaction_id, p.line_id
            """,
            params,
        ).fetchall()


def get_equity_lines():
    with db.transaction() as conn:
        return conn.execute(
            """
            SELECT
                t.posted_date AS date,
                a.name AS equity,
                money.name AS account,
                COALESCE(m.canonical_name, '') AS payee,
                t.raw_description AS description,
                l.amount_cents
            FROM ledger l
            JOIN accounts a ON a.id = l.account_id
            JOIN transactions t ON t.id = l.transaction_id
            JOIN accounts money ON money.id = t.account_id
            LEFT JOIN payees m ON m.id = t.payee_id
            WHERE a.type = 'equity' AND a.is_active = 1
            ORDER BY t.posted_date, l.id
            """
        ).fetchall()


def get_counterparty_lines():
    with db.transaction() as conn:
        return conn.execute(
            """
            SELECT
                t.posted_date AS date,
                c.name AS counterparty,
                money.name AS account,
                COALESCE(m.canonical_name, '') AS payee,
                (
                    SELECT GROUP_CONCAT(a.name, ', ')
                    FROM ledger l
                    JOIN accounts a ON a.id = l.account_id
                    WHERE l.group_id = t.id AND l.account_id != t.account_id
                ) AS category,
                t.raw_description AS description,
                t.amount_cents
            FROM transactions t
            JOIN counterparties c ON c.id = t.counterparty_id
            JOIN accounts money ON money.id = t.account_id
            LEFT JOIN payees m ON m.id = t.payee_id
            ORDER BY t.posted_date, t.id
            """
        ).fetchall()


def monthly_income(limit=12):
    today = current_day()
    periods = [_month_start(today, -offset) for offset in range(limit - 1, -1, -1)]

    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT period, SUM(amount_cents) AS amount_cents
            FROM income_expense_lines
            WHERE account_type = 'income' AND period >= ?
            GROUP BY period
            """,
            (periods[0],),
        ).fetchall()

    by_period = {row["period"]: row["amount_cents"] for row in rows}
    return {
        "labels": [period[:7] for period in periods],
        "income": [Money(by_period.get(period, 0)).amount for period in periods],
    }


def budget_progress(start: str, end: str):
    first, last = date.fromisoformat(start), date.fromisoformat(end)

    with db.transaction() as conn:
        budgets = {
            row["period"]: row["amount_cents"]
            for row in conn.execute(
                """
                SELECT b.period, SUM(b.amount_cents) AS amount_cents
                FROM budgets b
                JOIN accounts a ON a.id = b.account_id
                WHERE a.type = 'expense' AND a.budget = 1 AND b.period BETWEEN ? AND ?
                GROUP BY b.period
                """,
                (first.replace(day=1).isoformat(), last.isoformat()),
            )
        }
        daily = {
            row["day"]: row["amount_cents"]
            for row in conn.execute(
                """
                SELECT posted_date AS day, SUM(amount_cents) AS amount_cents
                FROM income_expense_lines
                WHERE account_type = 'expense' AND account_budget = 1 AND posted_date BETWEEN ? AND ?
                GROUP BY posted_date
                """,
                (start, end),
            )
        }

    today = current_day()
    labels, spent, budget_line = [], [], []
    spent_cents = earlier_months_cents = 0
    day = first

    while day <= last:
        month_start = day.replace(day=1)
        month_days = monthrange(day.year, day.month)[1]
        from_day = first.day if month_start == first.replace(day=1) else 1
        month_budget = budgets.get(month_start.isoformat(), 0)

        spent_cents += daily.get(day.isoformat(), 0)
        labels.append(day.isoformat())
        spent.append(Money(spent_cents).amount if day <= today else None)
        budget_line.append(
            Money(earlier_months_cents + scaled(month_budget, day.day - from_day + 1, month_days)).amount
        )

        next_day = day + timedelta(days=1)
        if next_day.month != day.month:
            earlier_months_cents += scaled(month_budget, day.day - from_day + 1, month_days)
        day = next_day

    return {"labels": labels, "spent": spent, "budget": budget_line}


def _month_start(today: date, offset: int) -> str:
    total = today.year * 12 + today.month - 1 + offset
    return date(total // 12, total % 12 + 1, 1).isoformat()
