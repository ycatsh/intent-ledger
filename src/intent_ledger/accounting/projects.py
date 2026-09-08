from datetime import date, timedelta

from intent_ledger.accounting.repositories.projects import ProjectRepository
from intent_ledger.analytics.reports import percent_of_total
from intent_ledger.db import db
from intent_ledger.settings import today


def create_project(form) -> int:
    name = form.get("name", "").strip()
    if not name:
        raise ValueError("Project name is required.")

    project_type = form.get("type", "").strip() or None
    start_date = form.get("start_date") or None
    end_date = form.get("end_date") or None
    notes = form.get("notes", "").strip() or None
    budget = form.get("budget", type=float)
    budget_cents = round(budget * 100) if budget else None

    with db.transaction() as conn:
        return ProjectRepository(conn).create(name, project_type, start_date, end_date, budget_cents, notes)


def delete_project(project_id: int):
    with db.transaction() as conn:
        ProjectRepository(conn).delete(project_id)


def get_project_id_by_name(name: str) -> int | None:
    with db.transaction() as conn:
        return ProjectRepository(conn).get_id_by_name((name or "").strip())


def get_all_projects():
    with db.transaction() as conn:
        return ProjectRepository(conn).list_active()


def get_projects_overview():
    with db.transaction() as conn:
        return conn.execute("""
            SELECT
                p.id,
                p.name,
                p.type,
                p.archived,
                COUNT(DISTINCT t.id) AS transactions,
                -SUM(CASE WHEN a.type = 'income' AND l.amount_cents < 0
                    THEN l.amount_cents ELSE 0 END) / 100.0 AS income,
                 SUM(CASE WHEN a.type = 'expense' AND l.amount_cents > 0
                    THEN l.amount_cents ELSE 0 END) / 100.0 AS expenses
            FROM projects p
            LEFT JOIN transactions_projects tp
                ON tp.project_id = p.id
            LEFT JOIN transactions t
                ON t.transaction_hash = tp.transaction_hash
            LEFT JOIN ledger l
                ON l.transaction_id = t.id
            LEFT JOIN accounts a
                ON a.id = l.account_id
            GROUP BY p.id
            ORDER BY p.archived, p.name
        """).fetchall()


def get_project(project_id: int):
    with db.transaction() as conn:
        return ProjectRepository(conn).get(project_id)


def get_project_summary(project_id: int):
    with db.transaction() as conn:
        row = conn.execute(
            """
            SELECT
                -SUM(CASE WHEN a.type = 'income' AND l.amount_cents < 0
                    THEN l.amount_cents ELSE 0 END) / 100.0 AS income,
                 SUM(CASE WHEN a.type = 'expense' AND l.amount_cents > 0
                    THEN l.amount_cents ELSE 0 END) / 100.0 AS expenses,
                COUNT(DISTINCT t.id) AS transactions
            FROM transactions_projects tp
            JOIN transactions t
                ON t.transaction_hash = tp.transaction_hash
            JOIN ledger l
                ON l.transaction_id = t.id
            JOIN accounts a
                ON a.id = l.account_id
            WHERE tp.project_id = ?
        """,
            (project_id,),
        ).fetchone()

    income = row["income"] or 0
    expenses = row["expenses"] or 0

    return {
        "income": income,
        "expenses": expenses,
        "net": income - expenses,
        "transactions": row["transactions"] or 0,
    }


def get_project_category_summary(project_id: int):
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                a.name AS category,
                SUM(l.amount_cents) / 100.0 AS amount,
                COUNT(*) AS transactions
            FROM transactions_projects tp
            JOIN transactions t
                ON t.transaction_hash = tp.transaction_hash
            JOIN ledger l
                ON l.transaction_id = t.id
            JOIN accounts a
                ON a.id = l.account_id
            WHERE tp.project_id = ?
              AND a.type = 'expense'
              AND l.amount_cents > 0
            GROUP BY a.id
            ORDER BY amount DESC
        """,
            (project_id,),
        ).fetchall()

    percents = percent_of_total(rows)

    return [
        {
            "category": r["category"],
            "amount": -r["amount"],
            "transactions": r["transactions"],
            "percent": p,
        }
        for r, p in zip(rows, percents, strict=True)
    ]


def get_project_payee_summary(project_id: int, limit=None):
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                m.canonical_name AS payee,
                SUM(l.amount_cents) / 100.0 AS amount,
                COUNT(*) AS transactions
            FROM transactions_projects tp
            JOIN transactions t
                ON t.transaction_hash = tp.transaction_hash
            JOIN ledger l
                ON l.transaction_id = t.id
            JOIN accounts a
                ON a.id = l.account_id
            JOIN payees m
                ON m.id = t.payee_id
            WHERE tp.project_id = ?
              AND a.type = 'expense'
              AND l.amount_cents > 0
            GROUP BY m.id
            ORDER BY amount DESC
        """
            + (" LIMIT ?" if limit else ""),
            (project_id, limit) if limit else (project_id,),
        ).fetchall()

    percents = percent_of_total(rows)

    return [
        {
            "payee": r["payee"],
            "amount": -r["amount"],
            "transactions": r["transactions"],
            "percent": p,
        }
        for r, p in zip(rows, percents, strict=True)
    ]


def get_project_inflow_summary(project_id: int, limit=None):
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                m.canonical_name AS source,
                -SUM(l.amount_cents) / 100.0 AS amount,
                COUNT(*) AS transactions
            FROM transactions_projects tp
            JOIN transactions t
                ON t.transaction_hash = tp.transaction_hash
            JOIN ledger l
                ON l.transaction_id = t.id
            JOIN accounts a
                ON a.id = l.account_id
            JOIN payees m
                ON m.id = t.payee_id
            WHERE tp.project_id = ?
              AND a.type = 'income'
              AND l.amount_cents < 0
            GROUP BY m.id
            ORDER BY amount DESC
        """
            + (" LIMIT ?" if limit else ""),
            (project_id, limit) if limit else (project_id,),
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


def get_project_transactions(project_id: int):
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                t.posted_date date,
                COALESCE(m.canonical_name, 'Unknown') payee,
                a.name category,
                a.type account_type,
                ROUND(-l.amount_cents / 100.0, 2) amount,
                t.raw_description description,
                EXISTS (
                    SELECT 1 FROM transactions_splits ts
                    WHERE ts.transaction_hash = t.transaction_hash
                ) is_split
            FROM transactions_projects tp
            JOIN transactions t
                ON t.transaction_hash = tp.transaction_hash
            JOIN ledger l
                ON l.transaction_id = t.id
               AND l.account_id != t.account_id
            JOIN accounts a
                ON a.id = l.account_id
            LEFT JOIN payees m
                ON m.id = t.payee_id
            WHERE tp.project_id = ?
              AND a.type IN ('income', 'expense')
            ORDER BY t.posted_date, t.id, l.id
            """,
            [project_id],
        ).fetchall()

    return [
        (
            [
                r["date"],
                r["payee"],
                r["category"],
                r["account_type"].capitalize(),
                r["amount"] if r["amount"] < 0 else "",
                r["amount"] if r["amount"] > 0 else "",
                r["description"],
            ],
            bool(r["is_split"]),
        )
        for r in rows
    ]


def get_project_trend(project_id: int):
    empty = {
        "labels": [],
        "daily_expenses": [],
        "daily_income": [],
        "cumulative_expenses": [],
        "cumulative_income": [],
        "has_income": False,
    }

    with db.transaction() as conn:
        project = conn.execute(
            "SELECT start_date, end_date FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()

        rows = conn.execute(
            """
            SELECT
                t.posted_date AS day,
                -SUM(CASE WHEN a.type = 'income' AND l.amount_cents < 0
                    THEN l.amount_cents ELSE 0 END) / 100.0 AS income,
                 SUM(CASE WHEN a.type = 'expense' AND l.amount_cents > 0
                    THEN l.amount_cents ELSE 0 END) / 100.0 AS expenses
            FROM transactions_projects tp
            JOIN transactions t
                ON t.transaction_hash = tp.transaction_hash
            JOIN ledger l
                ON l.transaction_id = t.id
            JOIN accounts a
                ON a.id = l.account_id
            WHERE tp.project_id = ?
            GROUP BY day
            ORDER BY day
        """,
            (project_id,),
        ).fetchall()

    if project is None:
        return empty

    daily_income = {r["day"][:10]: r["income"] or 0 for r in rows}
    daily_expenses = {r["day"][:10]: r["expenses"] or 0 for r in rows}
    txn_days = sorted(daily_income.keys() | daily_expenses.keys())

    start = project["start_date"] or (txn_days[0] if txn_days else None)
    end = project["end_date"] or (txn_days[-1] if txn_days else None)

    if not start or not end:
        return empty

    start_date = date.fromisoformat(start[:10])
    end_date = min(date.fromisoformat(end[:10]), today())

    labels = []
    daily_exp = []
    daily_inc = []
    cumulative_expenses = []
    cumulative_income = []

    running_expenses = 0
    running_income = 0

    day = start_date
    while day <= end_date:
        key = day.isoformat()
        expense = daily_expenses.get(key, 0)
        income = daily_income.get(key, 0)

        running_expenses += expense
        running_income += income

        labels.append(day.strftime("%b %d"))
        daily_exp.append(round(expense, 2))
        daily_inc.append(round(income, 2))
        cumulative_expenses.append(round(running_expenses, 2))
        cumulative_income.append(round(running_income, 2))

        day += timedelta(days=1)

    return {
        "labels": labels,
        "daily_expenses": daily_exp,
        "daily_income": daily_inc,
        "cumulative_expenses": cumulative_expenses,
        "cumulative_income": cumulative_income,
        "has_income": running_income > 0,
    }


def assign_transactions_to_project(transaction_hashes: list[str], project_id: int):
    if not transaction_hashes:
        raise ValueError("Select at least one transaction.")
    if not project_id:
        raise ValueError("Select a project.")

    with db.transaction() as conn:
        repo = ProjectRepository(conn)

        if repo.get(project_id) is None:
            raise ValueError("Project not found.")

        repo.assign_transactions(transaction_hashes, project_id)


def unassign_transactions_from_project(transaction_hashes: list[str], project_id: int):
    if not transaction_hashes:
        raise ValueError("Select at least one transaction.")
    if not project_id:
        raise ValueError("Select a project.")

    with db.transaction() as conn:
        ProjectRepository(conn).unassign_transactions(transaction_hashes, project_id)
