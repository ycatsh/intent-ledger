from calendar import month_name, monthrange
from datetime import date

from intent_ledger.accounting.repositories.budget import BudgetRepository
from intent_ledger.db import db
from intent_ledger.domain.money import Money
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


def get_budget_page(year: int, month: int):
    primary_period = date(year, month, 1).isoformat()

    past_periods = []
    for offset in range(1, PRESET_MONTHS + 1):
        y, m = shift_year_month(year, month, -offset)
        past_periods.append(date(y, m, 1).isoformat())

    periods = [primary_period]

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

        period_data = {period: _period_rows(conn, period) for period in {primary_period, *past_periods}}

        rollover = _rollover_balances(conn, primary_period)
        trend_data = _trend_rows(conn, trend_periods)
        goals = _goals(conn)
        recent = _recent_transactions(conn, primary_period)

        income_row = conn.execute(
            """
            SELECT income FROM monthly_income WHERE period = ?
        """,
            (primary_period,),
        ).fetchone()

        unbudgeted = conn.execute(
            """
            SELECT COALESCE(SUM(l.amount_cents), 0) AS spent
            FROM ledger l
            JOIN transactions t ON t.id = l.transaction_id
            JOIN accounts a ON a.id = l.account_id
            WHERE a.type = 'expense'
              AND a.budget = 0
              AND l.amount_cents > 0
              AND date(t.posted_date, 'start of month') = ?
        """,
            (primary_period,),
        ).fetchone()["spent"]

    budgetable_income = Money(-(income_row["income"] or 0)).amount if income_row else 0.0
    unbudgeted_spending = Money(unbudgeted).amount

    elapsed = _month_elapsed(year, month)

    rows = []
    totals_cents = {period: {"assigned": 0, "spent": 0} for period in periods}
    rollover_total_cents = 0

    for cat in categories:
        cat_id = cat["id"]
        by_period = {}
        for period in periods:
            data = period_data[period].get(cat_id, {"assigned": 0, "spent": 0})
            assigned = data["assigned"]
            spent = data["spent"]
            by_period[period] = {
                "assigned": Money(assigned).amount,
                "actual": Money(spent).amount,
                "left": Money(assigned + spent).amount,
            }
            totals_cents[period]["assigned"] += assigned
            totals_cents[period]["spent"] += spent

        primary = period_data[primary_period].get(cat_id, {"assigned": 0, "spent": 0})
        assigned_cents = primary["assigned"]
        spent_cents = primary["spent"]

        rollover_cents = rollover.get(cat_id, 0)
        carry_in_cents = rollover_cents - assigned_cents - spent_cents
        rollover_total_cents += rollover_cents

        goal = goals.get(cat_id)
        pace = _pace(assigned_cents, spent_cents, elapsed)
        status = _status_key(assigned_cents, spent_cents, rollover_cents, pace)

        rows.append(
            {
                "id": cat_id,
                "name": cat["name"],
                "group_name": cat["group_name"],
                "by_period": by_period,
                "carry_in": Money(carry_in_cents).amount,
                "rollover": Money(rollover_cents).amount,
                "presets": _presets(cat_id, period_data, past_periods, goal, rollover_cents, primary_period),
                "goal": goal,
                "pace": pace,
                "status_text": _status_text(status, rollover_cents),
                "status_color": STATUS_COLOR[status],
                "trend": {
                    "labels": [_short_label(period) for period in trend_periods],
                    "amounts": [
                        Money(-trend_data.get(cat_id, {}).get(period, 0)).amount for period in trend_periods
                    ],
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
        "income": budgetable_income,
        "unbudgeted_spending": unbudgeted_spending,
        "to_budget": budgetable_income - totals[primary_period]["assigned"] - unbudgeted_spending,
    }


def shift_year_month(year: int, month: int, delta: int):
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def _period_rows(conn, period: str):
    rows = conn.execute(
        """
        SELECT
            a.id AS account_id,
            COALESCE(b.amount_cents, 0) AS assigned,
            COALESCE(m.spent, 0) AS spent
        FROM accounts a
        LEFT JOIN budgets b
            ON b.account_id = a.id AND b.period = ?
        LEFT JOIN monthly_account_totals m
            ON m.account_id = a.id AND m.period = ?
        WHERE a.type = 'expense' AND a.budget = 1
    """,
        (period, period),
    ).fetchall()

    return {r["account_id"]: r for r in rows}


def _rollover_balances(conn, period: str):
    """Cumulative envelope balance for each category, from the first period
    it was ever budgeted through the given period - budgeted amounts plus
    net spend summed across that whole span, not just the current month.
    """
    rows = conn.execute(
        """
        WITH bounds AS (
            SELECT account_id, MIN(period) AS start_period
            FROM budgets
            GROUP BY account_id
        )
        SELECT
            a.id AS account_id,
            COALESCE((
                SELECT SUM(b.amount_cents) FROM budgets b
                WHERE b.account_id = a.id
                  AND b.period >= bounds.start_period AND b.period <= ?
            ), 0)
            + COALESCE((
                SELECT SUM(m.spent) FROM monthly_account_totals m
                WHERE m.account_id = a.id
                  AND m.period >= bounds.start_period AND m.period <= ?
            ), 0) AS rollover
        FROM accounts a
        JOIN bounds ON bounds.account_id = a.id
        WHERE a.type = 'expense' AND a.budget = 1
    """,
        (period, period),
    ).fetchall()

    return {r["account_id"]: r["rollover"] for r in rows}


def _trend_rows(conn, periods):
    if not periods:
        return {}

    placeholders = ", ".join("?" for _ in periods)
    rows = conn.execute(
        f"""
        SELECT m.account_id, m.period, m.spent
        FROM monthly_account_totals m
        JOIN accounts a ON a.id = m.account_id
        WHERE a.type = 'expense' AND a.budget = 1
          AND m.period IN ({placeholders})
    """,
        periods,
    ).fetchall()

    trend = {}
    for row in rows:
        trend.setdefault(row["account_id"], {})[row["period"]] = row["spent"]

    return trend


def _goals(conn):
    rows = conn.execute("""
        SELECT id AS account_id, goal_target_cents AS target_cents, goal_target_date AS target_date
        FROM accounts
        WHERE type = 'expense' AND budget = 1 AND goal_target_cents IS NOT NULL
    """).fetchall()

    return {
        r["account_id"]: {
            "target": Money(r["target_cents"]).amount,
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


def _pace(assigned_cents, spent_cents, elapsed):
    if elapsed is None or assigned_cents <= 0:
        return None

    spent_fraction = -spent_cents / assigned_cents

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


def _presets(cat_id, period_data, past_periods, goal, rollover_cents, primary_period):
    past_spend = [
        Money(-period_data[period].get(cat_id, {"spent": 0})["spent"]).amount for period in past_periods
    ]
    last = Money(period_data[past_periods[0]].get(cat_id, {"assigned": 0})["assigned"]).amount

    presets = {
        "last": last,
        "avg3": sum(past_spend) / len(past_spend) if past_spend else 0.0,
    }

    suggestion = _goal_suggestion(goal, rollover_cents, primary_period)
    if suggestion is not None:
        presets["goal"] = suggestion

    return presets


def _goal_suggestion(goal, rollover_cents, primary_period):
    if not goal:
        return None

    remaining = goal["target"] - Money(rollover_cents).amount
    if remaining <= 0:
        return 0.0

    if not goal["target_date"]:
        return round(remaining, 2)

    start = date.fromisoformat(primary_period)
    target = date.fromisoformat(goal["target_date"])
    months_left = max(
        (target.year * 12 + target.month) - (start.year * 12 + start.month) + 1,
        1,
    )

    return round(remaining / months_left, 2)


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
            {
                "name": row["group_name"],
                "row_ids": [],
                "by_period": {period: {"assigned": 0.0, "actual": 0.0, "left": 0.0} for period in periods},
                "carry_in": 0.0,
            },
        )

        group["row_ids"].append(row["id"])
        group["carry_in"] += row["carry_in"]

        for period in periods:
            cell = row["by_period"][period]
            subtotal = group["by_period"][period]
            subtotal["assigned"] += cell["assigned"]
            subtotal["actual"] += cell["actual"]
            subtotal["left"] += cell["left"]

    return list(groups.values())


def save_budget(form, period: str):
    with db.transaction() as conn:
        categories = conn.execute("""
            SELECT id FROM accounts WHERE type = 'expense' AND budget = 1
        """).fetchall()

    with db.transaction() as conn:
        budget_repo = BudgetRepository(conn)
        for cat in categories:
            value = form.get(f"budget_{cat['id']}", type=float)
            amount_cents = round(value * 100) if value else 0
            budget_repo.set_amount_cents(cat["id"], period, amount_cents)


def save_goal(form, account_id: int):
    target = form.get(f"goal_amount_{account_id}", type=float)
    target_date = form.get(f"goal_date_{account_id}") or None

    if target is None:
        return False

    with db.transaction() as conn:
        BudgetRepository(conn).set_goal(account_id, round(target * 100), target_date)

    return True


def delete_goal(account_id: int):
    with db.transaction() as conn:
        BudgetRepository(conn).delete_goal(account_id)


def move_budget(form, account_id: int, period: str):
    destination_id = form.get(f"move_to_{account_id}", type=int)
    amount = form.get(f"move_amount_{account_id}", type=float)

    if not destination_id or not amount:
        return 0.0  # missing/invalid input

    if destination_id == account_id:
        return 0.0  # moving a category's budget to itself is a no-op, not an error

    amount_cents = round(abs(amount) * 100)

    with db.transaction() as conn:
        budget_repo = BudgetRepository(conn)
        available = budget_repo.get_amount_cents(account_id, period)
        moved = min(amount_cents, max(available, 0))

        if moved <= 0:
            return 0.0

        for target_id, delta in ((account_id, -moved), (destination_id, moved)):
            budget_repo.adjust_amount_cents(target_id, period, delta)

    return Money(moved).amount
