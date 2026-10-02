from datetime import date

from intent_ledger import forms
from intent_ledger.accounting.repositories.projects import ProjectRepository
from intent_ledger.db import db
from intent_ledger.domain.money import Money
from intent_ledger.settings import today


def create_project(form) -> int:
    name = forms.text(form, "name")
    if not name:
        raise ValueError("Project name is required.")

    project_type = forms.text(form, "type") or None
    start_date = forms.optional_iso_date(form, "start_date", "Start date")
    end_date = forms.optional_iso_date(form, "end_date", "End date")
    notes = forms.text(form, "notes") or None

    budget = forms.optional_money(form, "budget", "Budget")
    if budget is not None and budget.cents < 0:
        raise ValueError("Budget can't be negative.")
    budget_cents = budget.cents if budget and budget.cents else None

    with db.transaction() as conn:
        repo = ProjectRepository(conn)
        if repo.get_id_by_name(name) is not None:
            raise ValueError(f"A project named {name!r} already exists.")

        return repo.create(name, project_type, start_date, end_date, budget_cents, notes)


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
            WITH totals AS (
                SELECT
                    tp.project_id,
                    SUM(CASE WHEN x.account_type = 'income' THEN x.amount_cents ELSE 0 END) AS income_cents,
                    SUM(CASE WHEN x.account_type = 'expense' THEN x.amount_cents ELSE 0 END) AS expenses_cents
                FROM transactions_projects tp
                JOIN income_expense_lines x ON x.transaction_hash = tp.transaction_hash
                GROUP BY tp.project_id
            )
            SELECT
                p.id,
                p.name,
                p.type,
                p.archived,
                (SELECT COUNT(*) FROM transactions_projects tp WHERE tp.project_id = p.id) AS transactions,
                COALESCE(totals.income_cents, 0) AS income_cents,
                COALESCE(totals.expenses_cents, 0) AS expenses_cents
            FROM projects p
            LEFT JOIN totals ON totals.project_id = p.id
            ORDER BY p.archived, p.name
        """).fetchall()


def get_project(project_id: int):
    with db.transaction() as conn:
        return ProjectRepository(conn).get(project_id)


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
            "SELECT start_date, end_date FROM projects WHERE id = ?", (project_id,)
        ).fetchone()

        if project is None:
            return empty

        txn_range = conn.execute(
            """
            SELECT MIN(posted_date) AS min_day, MAX(posted_date) AS max_day
            FROM income_expense_lines
            WHERE transaction_hash IN (
                SELECT transaction_hash FROM transactions_projects WHERE project_id = ?
            )
            """,
            (project_id,),
        ).fetchone()

        first = [d for d in (project["start_date"], txn_range["min_day"]) if d]
        last = [d for d in (project["end_date"], txn_range["max_day"]) if d]
        if not first or not last:
            return empty

        start_date = date.fromisoformat(min(first))
        end_date = min(date.fromisoformat(max(last)), today())
        if txn_range["max_day"]:
            end_date = max(end_date, date.fromisoformat(txn_range["max_day"]))

        rows = conn.execute(
            """
            WITH RECURSIVE calendar(day) AS (
                SELECT date(:start)
                UNION ALL
                SELECT date(day, '+1 day') FROM calendar WHERE day < date(:end)
            ),
            daily AS (
                SELECT
                    posted_date AS day,
                    SUM(CASE WHEN account_type = 'income' THEN amount_cents ELSE 0 END) AS income_cents,
                    SUM(CASE WHEN account_type = 'expense' THEN amount_cents ELSE 0 END) AS expense_cents
                FROM income_expense_lines
                WHERE transaction_hash IN (
                    SELECT transaction_hash FROM transactions_projects WHERE project_id = :project_id
                )
                GROUP BY posted_date
            )
            SELECT
                c.day AS day,
                COALESCE(d.expense_cents, 0) AS expense_cents,
                COALESCE(d.income_cents, 0) AS income_cents,
                SUM(COALESCE(d.expense_cents, 0)) OVER (ORDER BY c.day) AS cumulative_expense_cents,
                SUM(COALESCE(d.income_cents, 0)) OVER (ORDER BY c.day) AS cumulative_income_cents
            FROM calendar c
            LEFT JOIN daily d
                ON d.day = c.day
            ORDER BY c.day
        """,
            {"start": start_date.isoformat(), "end": end_date.isoformat(), "project_id": project_id},
        ).fetchall()

    labels = [date.fromisoformat(r["day"]).strftime("%b %d") for r in rows]
    daily_exp = [round(Money(r["expense_cents"]).amount, 2) for r in rows]
    daily_inc = [round(Money(r["income_cents"]).amount, 2) for r in rows]
    cumulative_expenses = [round(Money(r["cumulative_expense_cents"]).amount, 2) for r in rows]
    cumulative_income = [round(Money(r["cumulative_income_cents"]).amount, 2) for r in rows]

    return {
        "labels": labels,
        "daily_expenses": daily_exp,
        "daily_income": daily_inc,
        "cumulative_expenses": cumulative_expenses,
        "cumulative_income": cumulative_income,
        "has_income": bool(cumulative_income and cumulative_income[-1] > 0),
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

        placeholders = ",".join("?" * len(transaction_hashes))
        known = conn.execute(
            f"SELECT COUNT(*) AS n FROM transactions WHERE transaction_hash IN ({placeholders})",
            transaction_hashes,
        ).fetchone()["n"]
        if known != len(set(transaction_hashes)):
            raise ValueError("Transaction not found.")

        repo.assign_transactions(transaction_hashes, project_id)


def unassign_transactions_from_project(transaction_hashes: list[str], project_id: int):
    if not transaction_hashes:
        raise ValueError("Select at least one transaction.")
    if not project_id:
        raise ValueError("Select a project.")

    with db.transaction() as conn:
        ProjectRepository(conn).unassign_transactions(transaction_hashes, project_id)
