from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from intent_ledger import forms
from intent_ledger.accounting.budget import (
    PRESET_MONTHS,
    STATUS_HEX,
    delete_goal,
    get_budget_page,
    move_budget,
    save_budget,
    save_goal,
)
from intent_ledger.routes.period import get_period, period_links, requested_period

budget_bp = Blueprint("budget", __name__)


@budget_bp.get("/budget")
def budget():
    year, month = requested_period()

    return render_template(
        "budget.html",
        budget=get_budget_page(year, month, session.get("budget_compare", [])),
        nav=period_links("budget.budget", year, month),
        status_hex=STATUS_HEX,
    )


@budget_bp.post("/budget")
def budget_post():
    year, month = get_period()
    primary_period = date(year, month, 1).isoformat()
    action, _, target = request.form.get("action", "").partition(":")

    try:
        _apply_action(action, target, primary_period)
    except ValueError as e:
        flash(f"{e}", "error")

    return redirect(url_for("budget.budget"))


def _apply_action(action: str, target: str, primary_period: str) -> None:
    if action == "compare":
        offset = forms.whole_number(target, "month")
        if offset not in range(1, PRESET_MONTHS + 1):
            raise ValueError("Choose a valid month to compare.")
        offsets = session.get("budget_compare", [])
        session["budget_compare"] = (
            [o for o in offsets if o != offset] if offset in offsets else [*offsets, offset]
        )
    elif action == "save":
        save_budget(request.form, primary_period)
        flash("Budget saved.", "success")
    elif action == "save_goal":
        if save_goal(request.form, forms.whole_number(target, "category")):
            flash("Goal saved.", "success")
        else:
            flash("Enter a goal amount.", "error")
    elif action == "delete_goal":
        delete_goal(forms.whole_number(target, "category"))
        flash("Goal removed.", "success")
    elif action == "move_budget":
        moved = move_budget(request.form, forms.whole_number(target, "category"), primary_period)
        if moved:
            flash(f"Moved {moved:,.2f}.", "success")
        else:
            flash("Nothing to move.", "error")
