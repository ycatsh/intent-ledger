from flask import Blueprint, flash, render_template, request

from intent_ledger.accounting.accounts import get_all_payees
from intent_ledger.accounting.subscriptions import (
    cancel_subscription,
    create_subscription,
    delete_subscription,
    get_subscriptions_page,
    reactivate_subscription,
    update_subscription,
)
from intent_ledger.routes.navigation import active_tab, redirect_back

subscriptions_bp = Blueprint("subscriptions", __name__)

TABS = ("active", "candidates", "cancelled")


@subscriptions_bp.get("/subscriptions")
def subscriptions():
    return render_template(
        "subscriptions.html",
        active_tab=active_tab(TABS, "active"),
        subscriptions=get_subscriptions_page(),
        payees=get_all_payees(),
    )


@subscriptions_bp.post("/subscriptions/new")
def subscriptions_new():
    try:
        create_subscription(request.form)
        flash("Subscription added.", "success")
    except ValueError as e:
        flash(f"{e}", "error")
    return redirect_back("subscriptions.subscriptions")


@subscriptions_bp.post("/subscriptions/<int:subscription_id>/edit")
def subscriptions_edit(subscription_id):
    try:
        update_subscription(subscription_id, request.form)
        flash("Subscription updated.", "success")
    except ValueError as e:
        flash(f"{e}", "error")
    return redirect_back("subscriptions.subscriptions")


@subscriptions_bp.post("/subscriptions/<int:subscription_id>/cancel")
def subscriptions_cancel(subscription_id):
    cancel_subscription(subscription_id)
    flash("Subscription cancelled.", "info")
    return redirect_back("subscriptions.subscriptions")


@subscriptions_bp.post("/subscriptions/<int:subscription_id>/reactivate")
def subscriptions_reactivate(subscription_id):
    reactivate_subscription(subscription_id)
    flash("Subscription reactivated.", "success")
    return redirect_back("subscriptions.subscriptions")


@subscriptions_bp.post("/subscriptions/<int:subscription_id>/delete")
def subscriptions_delete(subscription_id):
    try:
        delete_subscription(subscription_id)
        flash("Subscription deleted.", "info")
    except ValueError as e:
        flash(f"{e}", "error")
    return redirect_back("subscriptions.subscriptions")
