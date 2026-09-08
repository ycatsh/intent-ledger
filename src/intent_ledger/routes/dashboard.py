from flask import Blueprint, flash, redirect, url_for

from intent_ledger.service import import_pending_statements, rebuild_ledger_and_subscriptions

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.get("/")
def home():
    return redirect(url_for("budget.budget"))


@dashboard_bp.post("/refresh")
def refresh():
    summaries = import_pending_statements()
    inserted = sum(s["inserted"] for s in summaries)
    rebuild_ledger_and_subscriptions()

    flash(f"Imported {inserted} transaction(s) and rebuilt the ledger.", "success")
    return redirect(url_for("inbox.inbox"))
