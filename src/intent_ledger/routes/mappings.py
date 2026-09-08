from flask import Blueprint, jsonify, render_template, request

from intent_ledger.accounting.mappings import get_mappings_page, save_mappings
from intent_ledger.accounting.payees import quick_create_payee
from intent_ledger.routes.navigation import active_tab

mappings_bp = Blueprint("mappings", __name__)

TABS = ("balance_sheet", "categories", "payees", "aliases", "counterparties")


@mappings_bp.get("/mappings")
def mappings():
    balance_sheet_accounts, category_accounts, payees, aliases, counterparties = get_mappings_page()
    return render_template(
        "mappings.html",
        active_tab=active_tab(TABS, "balance_sheet"),
        balance_sheet_accounts=balance_sheet_accounts,
        category_accounts=category_accounts,
        accounts=balance_sheet_accounts + category_accounts,
        payees=payees,
        aliases=aliases,
        counterparties=counterparties,
        needs_review_count=sum(1 for a in category_accounts if a["needs_review"]),
    )


@mappings_bp.post("/mappings/save")
def mappings_save():
    changes = request.get_json(force=True).get("changes", [])

    try:
        id_map = save_mappings(changes)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400

    return jsonify(ok=True, applied=len(changes), id_map=id_map)


@mappings_bp.post("/payees/quick-create")
def payees_quick_create():
    data = request.get_json(force=True) or {}

    try:
        payee = quick_create_payee(data.get("name", ""), data.get("account_id"))
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400

    return jsonify(ok=True, id=payee["id"], name=payee["name"])
