from flask import Blueprint, flash, redirect, render_template, request, url_for

from intent_ledger.accounting.accounts import (
    get_all_account_names,
    get_all_accounts,
    get_all_payees,
    get_payee_default_categories,
)
from intent_ledger.accounting.inbox import assign_all, get_unknown_txn_count, get_unknown_txn_groups
from intent_ledger.service import rebuild_ledger_and_subscriptions

inbox_bp = Blueprint("inbox", __name__)


@inbox_bp.get("/inbox")
def inbox():
    smart = request.args.get("smart") == "1"
    flat = request.args.get("flat") == "1"

    return render_template(
        "inbox.html",
        count=get_unknown_txn_count(),
        groups=get_unknown_txn_groups(smart=smart, flat=flat),
        accounts=get_all_accounts(),
        payees=get_all_payees(),
        account_names=get_all_account_names(),
        payee_defaults=get_payee_default_categories(),
        smart=smart,
        flat=flat,
        checked_ids=set(),
        submitted_payees={},
        submitted_accounts={},
    )


@inbox_bp.post("/assign")
def inbox_assign_payee():
    group_ids = request.form.getlist("ids", type=int)
    smart = request.form.get("smart") == "1"
    flat = request.form.get("flat") == "1"
    assignments = []

    for group_id in group_ids:
        payee = request.form.get(f"payee_{group_id}", "")
        account = request.form.get(f"account_{group_id}", "")
        member_ids = [int(x) for x in request.form.get(f"members_{group_id}", "").split(",") if x]
        assignments.extend((txn_id, payee, account) for txn_id in member_ids)

    redirect_args = {"smart": "1" if smart else None, "flat": "1" if flat else None}

    if not assignments:
        flash("No changes made.", "info")
        return redirect(url_for("inbox.inbox", **redirect_args))

    try:
        assign_all(assignments)
        rebuild_ledger_and_subscriptions()
        flash(f"Categorized {len(assignments)} transaction(s).", "success")
        return redirect(url_for("inbox.inbox", **redirect_args))
    except ValueError as e:
        flash(f"{e}", "error")

        return render_template(
            "inbox.html",
            count=get_unknown_txn_count(),
            groups=get_unknown_txn_groups(smart=smart, flat=flat),
            accounts=get_all_accounts(),
            payees=get_all_payees(),
            account_names=get_all_account_names(),
            payee_defaults=get_payee_default_categories(),
            smart=smart,
            flat=flat,
            checked_ids={str(g) for g in group_ids},
            submitted_payees={str(g): request.form.get(f"payee_{g}", "") for g in group_ids},
            submitted_accounts={str(g): request.form.get(f"account_{g}", "") for g in group_ids},
        )
