from flask import Blueprint, flash, redirect, render_template, request, url_for

from intent_ledger.accounting.accounts import get_all_payees
from intent_ledger.accounting.rules import (
    add_account_rule,
    delete_account_rule,
    get_account_rules,
    get_accounts,
    get_rule,
    preview_rule_matches,
    update_account_rule,
)
from intent_ledger.accounting.rules_overrides import (
    add_override,
    delete_override,
    get_override_context,
    get_overrides,
    save_split,
)
from intent_ledger.accounting.rules_transfers import (
    add_transfer_rule,
    delete_transfer_rule,
    get_transfer_rule,
    get_transfer_rules,
    preview_transfer_matches,
    update_transfer_rule,
)
from intent_ledger.routes.flash import rebuild_impact_message
from intent_ledger.routes.navigation import redirect_back
from intent_ledger.service import rebuild_ledger_and_subscriptions

rules_bp = Blueprint("rules", __name__)

TABS = ("rules", "transfers", "overrides")


@rules_bp.get("/rules")
def rules():
    transaction_hash = request.args.get("transaction_hash")
    rule_id = request.args.get("rule_id", type=int)
    test = request.args.get("test", type=int)
    transfer_rule_id = request.args.get("transfer_rule_id", type=int)
    tab = request.args.get("tab")

    context = get_override_context(transaction_hash) if transaction_hash else None
    editing_rule = get_rule(rule_id) if rule_id else None
    editing_transfer_rule = get_transfer_rule(transfer_rule_id) if transfer_rule_id else None

    test_matches = None
    if editing_rule and test:
        try:
            test_matches = preview_rule_matches(editing_rule["match_type"], editing_rule["pattern"])
        except ValueError as e:
            flash(f"{e}", "error")

    transfer_test = None
    if editing_transfer_rule and test:
        try:
            transfer_test = preview_transfer_matches(transfer_rule_id)
        except ValueError as e:
            flash(f"{e}", "error")

    if tab in TABS:
        active_tab = tab
    elif transaction_hash:
        active_tab = "overrides"
    elif transfer_rule_id:
        active_tab = "transfers"
    else:
        active_tab = "rules"

    return render_template(
        "rules.html",
        rules=get_account_rules(),
        overrides=get_overrides(),
        accounts=get_accounts(),
        payees=get_all_payees(),
        context=context,
        editing_rule=editing_rule,
        test_matches=test_matches,
        transfer_rules=get_transfer_rules(),
        editing_transfer_rule=editing_transfer_rule,
        transfer_test=transfer_test,
        active_tab=active_tab,
    )


@rules_bp.post("/rules/new")
def rules_new():
    try:
        new_id = add_account_rule(request.form)
        changed = rebuild_ledger_and_subscriptions()
        flash("Rule added." + rebuild_impact_message(changed), "success")
        return redirect(url_for("rules.rules", rule_id=new_id, tab="rules"))
    except ValueError as e:
        flash(f"{e}", "error")
        return redirect(url_for("rules.rules", tab="rules"))


@rules_bp.post("/rules/<int:rule_id>/update")
def rules_update(rule_id):
    try:
        update_account_rule(rule_id, request.form)
        changed = rebuild_ledger_and_subscriptions()
        flash("Rule updated." + rebuild_impact_message(changed), "success")
    except ValueError as e:
        flash(f"{e}", "error")
    return redirect(url_for("rules.rules", rule_id=rule_id, tab="rules"))


@rules_bp.post("/rules/<int:rule_id>/delete")
def rules_delete(rule_id):
    delete_account_rule(rule_id)
    changed = rebuild_ledger_and_subscriptions()
    flash("Rule removed." + rebuild_impact_message(changed), "info")
    return redirect(url_for("rules.rules", tab="rules"))


@rules_bp.post("/transfer-rules/new")
def transfer_rules_new():
    try:
        new_id = add_transfer_rule(request.form)
        changed = rebuild_ledger_and_subscriptions()
        flash("Transfer rule added." + rebuild_impact_message(changed), "success")
        return redirect(url_for("rules.rules", transfer_rule_id=new_id, tab="transfers"))
    except ValueError as e:
        flash(f"{e}", "error")
        return redirect(url_for("rules.rules", tab="transfers"))


@rules_bp.post("/transfer-rules/<int:rule_id>/update")
def transfer_rules_update(rule_id):
    try:
        update_transfer_rule(rule_id, request.form)
        changed = rebuild_ledger_and_subscriptions()
        flash("Transfer rule updated." + rebuild_impact_message(changed), "success")
    except ValueError as e:
        flash(f"{e}", "error")
    return redirect(url_for("rules.rules", transfer_rule_id=rule_id, tab="transfers"))


@rules_bp.post("/transfer-rules/<int:rule_id>/delete")
def transfer_rules_delete(rule_id):
    delete_transfer_rule(rule_id)
    changed = rebuild_ledger_and_subscriptions()
    flash("Transfer rule removed." + rebuild_impact_message(changed), "info")
    return redirect(url_for("rules.rules", tab="transfers"))


@rules_bp.post("/overrides/new")
def overrides_add():
    transaction_hash = request.form.get("transaction_hash", "")

    try:
        if request.form.get("mode") == "split":
            save_split(request.form)
            message = "Transaction split."
        else:
            add_override(request.form)
            message = "Override added."
        changed = rebuild_ledger_and_subscriptions()
        flash(message + rebuild_impact_message(changed), "success")
    except ValueError as e:
        flash(f"{e}", "error")
        return redirect(url_for("rules.rules", transaction_hash=transaction_hash, tab="overrides"))

    return redirect_back("rules.rules", tab="overrides")


@rules_bp.post("/overrides/<transaction_hash>/delete")
def overrides_delete(transaction_hash):
    delete_override(transaction_hash)
    changed = rebuild_ledger_and_subscriptions()
    flash("Override removed." + rebuild_impact_message(changed), "info")
    return redirect_back("rules.rules", tab="overrides")
