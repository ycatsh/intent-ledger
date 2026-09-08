from flask import Blueprint, flash, redirect, render_template, request, url_for

from intent_ledger.accounting.accounts import get_active_accounts, get_all_accounts
from intent_ledger.importer.parsers import PARSERS
from intent_ledger.importer.uploads import (
    delete_pending_statement,
    list_pending_statements,
    save_uploaded_statements,
)

import_bp = Blueprint("import", __name__)


@import_bp.get("/import")
def import_page():
    accounts = [a for a in get_active_accounts() if a["type"] in ("asset", "liability")]
    account_names = {a["id"]: a["name"] for a in get_all_accounts()}

    pending = list_pending_statements()
    for statement in pending:
        statement["account_name"] = account_names.get(statement["account_id"])

    return render_template(
        "import.html",
        pending=pending,
        accounts=accounts,
        parsers=list(PARSERS.values()),
    )


@import_bp.post("/import/upload")
def import_upload():
    files = [f for f in request.files.getlist("statements") if f.filename]
    account_id = request.form.get("account_id", type=int)
    parser_slug = request.form.get("parser_slug", "canonical")

    if not files:
        flash("No files selected.", "error")
        return redirect(url_for("import.import_page"))

    if account_id is None:
        flash("Choose an account before uploading.", "error")
        return redirect(url_for("import.import_page"))

    result = save_uploaded_statements(files, account_id, parser_slug, request.headers.get("User-Agent", ""))

    if result["saved"]:
        flash(f"Uploaded {len(result['saved'])} statement(s).", "success")
    for error in result["errors"]:
        flash(error, "error")

    return redirect(url_for("import.import_page"))


@import_bp.post("/import/<filename>/delete")
def import_delete(filename):
    try:
        delete_pending_statement(filename)
        flash("Statement removed.", "info")
    except ValueError as e:
        flash(str(e), "error")

    return redirect(url_for("import.import_page"))
