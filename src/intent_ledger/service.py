from pathlib import Path

from intent_ledger.accounting.accounts import get_all_accounts
from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.projects import get_all_projects
from intent_ledger.accounting.subscriptions import rebuild_subscription_matches
from intent_ledger.analytics.workbook import (
    export_account_statement,
    export_project_report,
    export_yearly_report,
)
from intent_ledger.db import db
from intent_ledger.importer.importer import import_statement
from intent_ledger.importer.uploads import INGEST_DIR, list_pending_statements


def initialize_database():
    db.initialize()


def import_pending_statements() -> list[dict]:
    summaries = []

    for entry in list_pending_statements():
        if entry["account_id"] is None or entry["parser_slug"] is None:
            continue

        summary = import_statement(
            INGEST_DIR / entry["name"],
            account_id=entry["account_id"],
            parser_slug=entry["parser_slug"],
        )
        summaries.append(summary)

    return summaries


def rebuild_ledger_and_subscriptions() -> int:
    """Rebuild the ledger and subscription matches. Returns how many
    transactions ended up in a different account than before, so a caller
    can report the actual impact of whatever change triggered the rebuild.
    """
    changed = rebuild_ledger()
    rebuild_subscription_matches()
    return changed


def export_all(accounts: bool = True, projects: bool = True, yearly: bool = True) -> list[Path]:
    exported = []

    if accounts:
        exported += [export_account_statement(account.id) for account in get_all_accounts()]

    if projects:
        exported += [export_project_report(project["id"]) for project in get_all_projects()]

    if yearly:
        exported += [export_yearly_report(year) for year in _years_with_transactions()]

    return exported


def _years_with_transactions() -> list[int]:
    with db.transaction() as conn:
        rows = conn.execute("""
            SELECT DISTINCT CAST(strftime('%Y', posted_date) AS INTEGER) AS year
            FROM transactions
            ORDER BY year
        """).fetchall()

    return [row["year"] for row in rows]
