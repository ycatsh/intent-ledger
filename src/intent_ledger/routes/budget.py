from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for

from intent_ledger.accounting.budget import (
    STATUS_HEX,
    delete_goal,
    get_budget_page,
    move_budget,
    save_budget,
    save_goal,
)
from intent_ledger.routes.period import get_period, set_period, shift_period
from intent_ledger.settings import today

budget_bp = Blueprint("budget", __name__)


@budget_bp.get("/budget")
def budget():
    year, month = get_period()

    return render_template(
        "budget.html",
        budget=get_budget_page(year, month),
        status_hex=STATUS_HEX,
    )


@budget_bp.post("/budget")
def budget_post():
    year, month = get_period()
    primary_period = date(year, month, 1).isoformat()
    action = request.form.get("action", "")

    if action == "previous":
        shift_period(-1)
    elif action == "next":
        shift_period(1)
    elif action == "today":
        today_ = today()
        set_period(today_.year, today_.month)
    elif action == "save":
        save_budget(request.form, primary_period)
        flash("Budget saved.", "success")
    elif action.startswith("save_goal:"):
        account_id = int(action.split(":", 1)[1])
        if save_goal(request.form, account_id):
            flash("Goal saved.", "success")
        else:
            flash("Enter a goal amount.", "error")
    elif action.startswith("delete_goal:"):
        delete_goal(int(action.split(":", 1)[1]))
        flash("Goal removed.", "success")
    elif action.startswith("move_budget:"):
        account_id = int(action.split(":", 1)[1])
        moved = move_budget(request.form, account_id, primary_period)
        if moved:
            flash(f"Moved {moved:,.2f}.", "success")
        else:
            flash("Nothing to move.", "error")

    return redirect(url_for("budget.budget"))
