from calendar import month_name, monthrange
from collections import defaultdict
from datetime import date

from intent_ledger import forms
from intent_ledger.accounting.repositories.budget import BudgetRepository
from intent_ledger.db import db
from intent_ledger.domain.money import Money, scaled
from intent_ledger.settings import today

PRESET_MONTHS = 3
TREND_MONTHS = 6
PACE_RISK_MARGIN = 0.15

STATUS_HEX = {
    "green": "#3d9970",
    "yellow": "#c9921a",
    "red": "#c0392b",
}

UNGROUPED = "Ungrouped"


def get_budget_page(year: int, month: int, compare_offsets=()):
    primary_period = date(year, month, 1).isoformat()

    past_periods = []
    for offset in range(1, PRESET_MONTHS + 1):
        y, m = shift_year_month(year, month, -offset)
        past_periods.append(date(y, m, 1).isoformat())

    periods = [primary_period, *(past_periods[offset - 1] for offset in sorted(compare_offsets))]

    trend_periods = []
    for offset in range(TREND_MONTHS - 1, -1, -1):
        y, m = shift_year_month(year, month, -offset)
        trend_periods.append(date(y, m, 1).isoformat())

    with db.transaction() as conn:
        categories = conn.execute(
            """
            SELECT
                a.id,
                a.name,
                COALESCE(parent.name, ?) AS group_name
            FROM accounts a
            LEFT JOIN accounts parent ON parent.id = a.parent_account_id
            WHERE a.type = 'expense' AND a.budget = 1
            ORDER BY group_name, a.name
        """,
            (UNGROUPED,),
        ).fetchall()

        assigned_by_cat, spent_by_cat = _monthly_history(conn)
        goals = _goals(conn)
        recent = _recent_transactions(conn, primary_period)
        month_totals = {
            (row["account_type"], row["account_budget"]): row["amount_cents"]
            for row in conn.execute(
                """
                SELECT account_type, account_budget, SUM(amount_cents) AS amount_cents
                FROM income_expense_lines
                WHERE period = ?
                GROUP BY account_type, account_budget
                """,
                (primary_period,),
            )
        }
        first_day = conn.execute("SELECT MIN(posted_date) AS day FROM transactions").fetchone()["day"]

    covered = [period for period in past_periods if first_day and period >= first_day[:8] + "01"]
    income_cents = month_totals.get(("income", 1), 0)
    unbudgeted_cents = month_totals.get(("expense", 0), 0)

    elapsed = _month_elapsed(year, month)

    rows = []
    totals_cents = {period: {"assigned": 0, "spent": 0} for period in periods}
    rollover_total_cents = 0

    for cat in categories:
        cat_id = cat["id"]
        by_period = {}
        cat_assigned = assigned_by_cat.get(cat_id, {})
        cat_spent = spent_by_cat.get(cat_id, {})
        for period in periods:
            assigned = cat_assigned.get(period, 0)
            spent = cat_spent.get(period, 0)
            by_period[period] = {
                "assigned": Money(assigned).amount,
                "actual": Money(spent).amount,
                "left": Money(assigned + spent).amount,
                "cents": (assigned, spent),
            }
            totals_cents[period]["assigned"] += assigned
            totals_cents[period]["spent"] += spent

        assigned_cents = cat_assigned.get(primary_period, 0)
        spent_cents = cat_spent.get(primary_period, 0)

        rollover_cents = envelope(cat_assigned, cat_spent, primary_period)
        carry_in_cents = rollover_cents - assigned_cents - spent_cents
        rollover_total_cents += rollover_cents

        goal = goals.get(cat_id)
        pace = _pace(carry_in_cents + assigned_cents, spent_cents, elapsed)
        status = _status_key(assigned_cents, spent_cents, rollover_cents, pace)

        rows.append(
            {
                "id": cat_id,
                "name": cat["name"],
                "group_name": cat["group_name"],
                "by_period": by_period,
                "carry_in": Money(carry_in_cents).amount,
                "rollover": Money(rollover_cents).amount,
                "presets": _presets(
                    cat_assigned, cat_spent, past_periods, covered, goal, carry_in_cents, primary_period
                ),
                "goal": goal,
                "pace": pace,
                "status_text": _status_text(status, rollover_cents),
                "status_color": STATUS_COLOR[status],
                "trend": {
                    "labels": [_short_label(period) for period in trend_periods],
                    "amounts": [Money(-cat_spent.get(period, 0)).amount for period in trend_periods],
                },
                "recent_transactions": recent.get(cat_id, []),
            }
        )

    totals = {
        period: {
            "assigned": Money(t["assigned"]).amount,
            "actual": Money(t["spent"]).amount,
            "left": Money(t["assigned"] + t["spent"]).amount,
        }
        for period, t in totals_cents.items()
    }

    return {
        "primary_period": primary_period,
        "compare_options": [
            {"offset": offset, "label": _period_label(period), "active": offset in compare_offsets}
            for offset, period in enumerate(past_periods, start=1)
        ],
        "periods": [
            {
                "period": period,
                "label": _period_label(period),
                "editable": period == primary_period,
            }
            for period in periods
        ],
        "trend_periods": trend_periods,
        "rows": rows,
        "groups": _group_rows(rows, periods),
        "totals": totals,
        "rollover_total": Money(rollover_total_cents).amount,
        "over_budget": [
            {
                "id": row["id"],
                "name": row["name"],
                "left": row["by_period"][primary_period]["left"],
            }
            for row in rows
            if row["by_period"][primary_period]["left"] < 0
        ],
        "income": Money(income_cents).amount,
        "unbudgeted_spending": Money(unbudgeted_cents).amount,
        "to_budget": Money(income_cents - totals_cents[primary_period]["assigned"] - unbudgeted_cents).amount,
    }


def shift_year_month(year: int, month: int, delta: int):
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def envelope(assigned: dict[str, int], spent: dict[str, int], period: str) -> int:
    """Return what is left in a category's envelope at the end of `period`.

    Carry starts at the first month the category was ever budgeted. Before
    that month, an envelope holds only that month's own activity.
    """
    anchor = min(assigned, default=None)
    if anchor is None or period < anchor:
        return assigned.get(period, 0) + spent.get(period, 0)

    return sum(cents for month, cents in assigned.items() if anchor <= month <= period) + sum(
        cents for month, cents in spent.items() if anchor <= month <= period
    )


def _monthly_history(conn):
    assigned = defaultdict(dict)
    for row in conn.execute("SELECT account_id, period, amount_cents FROM budgets"):
        assigned[row["account_id"]][row["period"]] = row["amount_cents"]

    spent = defaultdict(dict)
    for row in conn.execute(
        """
        SELECT account_id, period, SUM(amount_cents) AS amount_cents
        FROM income_expense_lines
        WHERE account_type = 'expense'
        GROUP BY account_id, period
        """
    ):
        spent[row["account_id"]][row["period"]] = -row["amount_cents"]

    return assigned, spent


def _goals(conn):
    rows = conn.execute("""
        SELECT id AS account_id, goal_target_cents AS target_cents, goal_target_date AS target_date
        FROM accounts
        WHERE type = 'expense' AND budget = 1 AND goal_target_cents IS NOT NULL
    """).fetchall()

    return {
        r["account_id"]: {
            "target": Money(r["target_cents"]).amount,
            "target_cents": r["target_cents"],
            "target_date": r["target_date"],
        }
        for r in rows
    }


def _recent_transactions(conn, period: str, limit: int = 5):
    rows = conn.execute(
        """
        SELECT account_id, posted_date, description, amount_cents FROM (
            SELECT
                l.account_id,
                t.posted_date,
                COALESCE(p.canonical_name, t.normalized_description, t.raw_description) AS description,
                l.amount_cents,
                ROW_NUMBER() OVER (
                    PARTITION BY l.account_id
                    ORDER BY t.posted_date DESC, t.id DESC
                ) AS rn
            FROM ledger l
            JOIN transactions t ON t.id = l.transaction_id
            JOIN accounts a ON a.id = l.account_id
            LEFT JOIN payees p ON p.id = t.payee_id
            WHERE a.type = 'expense' AND a.budget = 1
              AND date(t.posted_date, 'start of month') = ?
        )
        WHERE rn <= ?
        ORDER BY account_id, posted_date DESC
    """,
        (period, limit),
    ).fetchall()

    recent = {}
    for row in rows:
        recent.setdefault(row["account_id"], []).append(
            {
                "posted_date": row["posted_date"],
                "description": row["description"],
                "amount": Money(row["amount_cents"]).amount,
            }
        )

    return recent


def _month_elapsed(year: int, month: int):
    today_ = today()

    if (today_.year, today_.month) != (year, month):
        return None

    return today_.day / monthrange(year, month)[1]


def _pace(available_cents, spent_cents, elapsed):
    if elapsed is None or available_cents <= 0:
        return None

    spent_fraction = -spent_cents / available_cents

    return {
        "spent_fraction": spent_fraction,
        "elapsed_fraction": elapsed,
        "at_risk": spent_fraction - elapsed > PACE_RISK_MARGIN,
    }


STATUS_COLOR = {
    "over": "red",
    "unbudgeted_spend": "yellow",
    "no_budget": "green",
    "at_risk": "yellow",
    "nearly_spent": "yellow",
    "on_track": "green",
}

_STATUS_TEXT = {
    "unbudgeted_spend": "Unbudgeted spend",
    "no_budget": "No budget set",
    "at_risk": "At risk",
    "nearly_spent": "Nearly spent",
    "on_track": "On track",
}


def _status_key(assigned_cents, spent_cents, rollover_cents, pace):
    if rollover_cents < 0:
        return "over"
    if assigned_cents <= 0:
        return "unbudgeted_spend" if spent_cents < 0 else "no_budget"
    if pace and pace["at_risk"]:
        return "at_risk"
    if -spent_cents >= assigned_cents * 0.9:
        return "nearly_spent"

    return "on_track"


def _status_text(status: str, rollover_cents):
    if status == "over":
        return f"Over by {Money(abs(rollover_cents))}"

    return _STATUS_TEXT[status]


def _presets(assigned, spent, past_periods, covered, goal, carry_in_cents, primary_period):
    past_spend_cents = sum(-spent.get(period, 0) for period in covered)
    presets = {
        "last": Money(assigned.get(past_periods[0], 0)).amount,
        "avg3": Money(scaled(past_spend_cents, 1, len(covered)) if covered else 0).amount,
    }

    if goal:
        presets["goal"] = _goal_suggestion(goal, carry_in_cents, primary_period)

    return presets


def _goal_suggestion(goal, balance_cents, primary_period):
    """Spread what the goal still needs, before this month's budget, over the months left.

    The target month counts, so a goal for May set in March spreads over three months.
    """
    remaining_cents = goal["target_cents"] - balance_cents
    if remaining_cents <= 0:
        return 0.0
    if not goal["target_date"]:
        return Money(remaining_cents).amount

    start = date.fromisoformat(primary_period)
    target = date.fromisoformat(goal["target_date"])
    months_left = max((target.year * 12 + target.month) - (start.year * 12 + start.month) + 1, 1)
    return Money(scaled(remaining_cents, 1, months_left)).amount


def _short_label(period: str) -> str:
    d = date.fromisoformat(period)
    return f"{month_name[d.month][:3]} {str(d.year)[2:]}"


def _period_label(period: str) -> str:
    d = date.fromisoformat(period)
    return f"{month_name[d.month]} {d.year}"


def _group_rows(rows, periods):
    groups = {}

    for row in rows:
        group = groups.setdefault(
            row["group_name"],
            {"name": row["group_name"], "row_ids": [], "cents": {period: [0, 0] for period in periods}},
        )
        group["row_ids"].append(row["id"])
        for period in periods:
            assigned, spent = row["by_period"][period]["cents"]
            group["cents"][period][0] += assigned
            group["cents"][period][1] += spent

    for group in groups.values():
        group["by_period"] = {
            period: {
                "assigned": Money(assigned).amount,
                "actual": Money(spent).amount,
                "left": Money(assigned + spent).amount,
            }
            for period, (assigned, spent) in group.pop("cents").items()
        }

    return list(groups.values())


def save_budget(form, period: str):
    with db.transaction() as conn:
        categories = conn.execute("""
            SELECT id FROM accounts WHERE type = 'expense' AND budget = 1
        """).fetchall()

        budget_repo = BudgetRepository(conn)
        for cat in categories:
            if f"budget_{cat['id']}" not in form:
                continue
            amount = forms.optional_money(form, f"budget_{cat['id']}", "Every budget")
            if amount is not None and amount.cents < 0:
                raise ValueError("Budgets can't be negative.")
            budget_repo.set_amount_cents(cat["id"], period, amount.cents if amount else 0)


def save_goal(form, account_id: int):
    target = forms.optional_money(form, f"goal_amount_{account_id}", "Goal amount")
    target_date = forms.optional_iso_date(form, f"goal_date_{account_id}", "Goal date")

    if target is None:
        return False
    if target.cents < 0:
        raise ValueError("Goal amount can't be negative.")

    with db.transaction() as conn:
        BudgetRepository(conn).set_goal(account_id, target.cents, target_date)

    return True


def delete_goal(account_id: int):
    with db.transaction() as conn:
        BudgetRepository(conn).delete_goal(account_id)


def move_budget(form, account_id: int, period: str):
    destination_id = forms.optional_integer(form, f"move_to_{account_id}", "category")
    amount = forms.optional_money(form, f"move_amount_{account_id}", "Amount to move")

    if destination_id is None or amount is None or destination_id == account_id:
        return 0.0
    if amount.cents <= 0:
        raise ValueError("Amount to move must be more than zero.")

    with db.transaction() as conn:
        destination = conn.execute(
            "SELECT 1 FROM accounts WHERE id = ? AND type = 'expense' AND budget = 1", (destination_id,)
        ).fetchone()
        if destination is None:
            raise ValueError("Choose a budget category to move to.")

        budget_repo = BudgetRepository(conn)
        available = budget_repo.get_amount_cents(account_id, period)
        if amount.cents > available:
            raise ValueError(f"Only {Money(available)} is assigned to that category this month.")

        received = budget_repo.get_amount_cents(destination_id, period)
        budget_repo.set_amount_cents(account_id, period, available - amount.cents)
        budget_repo.set_amount_cents(destination_id, period, received + amount.cents)

    return amount.amount
