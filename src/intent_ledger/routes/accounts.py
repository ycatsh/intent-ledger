from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from intent_ledger.accounting.accounts import (
    create_account,
    get_account_view_page,
    get_active_accounts,
    get_all_accounts,
    get_all_payees,
    get_ledger_entries,
    get_payee_view_page,
)
from intent_ledger.accounting.counterparties import (
    assign_counterparty,
    get_all_counterparties,
    get_counterparty_id_by_name,
)
from intent_ledger.accounting.manual import add_manual_transaction, delete_manual_transaction
from intent_ledger.accounting.projects import (
    assign_transactions_to_project,
    get_all_projects,
    get_project_id_by_name,
    unassign_transactions_from_project,
)
from intent_ledger.analytics.workbook import export_account_statement
from intent_ledger.service import rebuild_ledger_and_subscriptions
from intent_ledger.settings import today

accounts_bp = Blueprint("accounts", __name__)


def _redirect_to_ledger_view():
    return redirect(
        url_for(
            "accounts.accounts",
            account_id=request.form.get("account_id", type=int),
            search=request.form.get("search", ""),
            sort=request.form.get("sort", ""),
        )
    )


def _perform(action, success_message):
    try:
        action()
        flash(success_message, "success")
    except ValueError as e:
        flash(f"{e}", "error")

    return _redirect_to_ledger_view()


@accounts_bp.get("/accounts")
def accounts():
    account_id = request.args.get("account_id", type=int)
    has_explicit_account = account_id is not None
    search = request.args.get("search", "")
    sort = request.args.get("sort")

    active_accounts = get_active_accounts()

    if account_id is None and active_accounts:
        account_id = active_accounts[0]["id"]

    return render_template(
        "accounts.html",
        accounts=active_accounts,
        all_accounts=get_all_accounts(),
        selected_account_id=account_id,
        has_explicit_account=has_explicit_account,
        payees=get_all_payees(),
        transactions=get_ledger_entries(account_id, search=search, sort=sort),
        projects=get_all_projects(),
        counterparties=get_all_counterparties(),
        filters={"search": search, "sort": sort},
        today=today().isoformat(),
    )


@accounts_bp.get("/accounts/view/<int:account_id>")
def account_view(account_id):
    page = get_account_view_page(account_id)
    if page is None:
        abort(404)

    return render_template("accounts_view.html", **page)


@accounts_bp.get("/payees/view/<int:payee_id>")
def payee_view(payee_id):
    page = get_payee_view_page(payee_id)
    if page is None:
        abort(404)

    return render_template("accounts_view.html", **page)


@accounts_bp.get("/accounts/export")
def accounts_export():
    account_id = request.args.get("account_id", type=int)
    if account_id is None:
        abort(404)

    search = request.args.get("search", "")

    try:
        path = export_account_statement(account_id, search=search)
    except ValueError:
        abort(404)

    return send_file(path, as_attachment=True, download_name=path.name)


@accounts_bp.post("/accounts/assign-project")
def assign_project():
    ids = request.form.getlist("ids")
    project_id = get_project_id_by_name(request.form.get("project_name", ""))

    return _perform(
        lambda: assign_transactions_to_project(ids, project_id),
        f"Assigned {len(ids)} transaction(s) to project.",
    )


@accounts_bp.post("/accounts/unassign-project")
def unassign_project():
    ids = request.form.getlist("ids")
    project_id = get_project_id_by_name(request.form.get("project_name", ""))

    return _perform(
        lambda: unassign_transactions_from_project(ids, project_id),
        f"Removed {len(ids)} transaction(s) from project.",
    )


@accounts_bp.post("/accounts/assign-counterparty")
def assign_counterparty_route():
    ids = request.form.getlist("ids")
    name = request.form.get("counterparty_name", "").strip()

    def action():
        counterparty_id = None
        if name:
            counterparty_id = get_counterparty_id_by_name(name)
            if counterparty_id is None:
                raise ValueError(f'Counterparty "{name}" not found. Add it under Mappings first.')

        assign_counterparty(ids, counterparty_id)

    return _perform(action, f"Updated counterparty for {len(ids)} transaction(s).")


@accounts_bp.post("/accounts/new")
def accounts_new():
    try:
        create_account(request.form.get("name", ""), request.form.get("type", ""))
        flash("Account added.", "success")
    except ValueError as e:
        flash(f"{e}", "error")

    return redirect(url_for("accounts.accounts"))


@accounts_bp.post("/transactions/new")
def transactions_new():
    def action():
        add_manual_transaction(
            {
                "from_account_id": request.form.get("from_account_id", type=int),
                "category_account_id": request.form.get("category_account_id", type=int),
                "payee_name": request.form.get("payee_name", ""),
                "posted_date": request.form.get("posted_date"),
                "amount": request.form.get("amount", type=float),
            }
        )
        rebuild_ledger_and_subscriptions()

    return _perform(action, "Transaction added.")


@accounts_bp.post("/transactions/<transaction_hash>/delete")
def transactions_delete(transaction_hash):
    def action():
        delete_manual_transaction(transaction_hash)
        rebuild_ledger_and_subscriptions()

    return _perform(action, "Transaction deleted.")
