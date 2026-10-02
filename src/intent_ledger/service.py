import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from intent_ledger.accounting.accounts import get_all_accounts
from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.projects import get_all_projects
from intent_ledger.analytics.workbook import (
    export_account_statement,
    export_project_report,
    export_yearly_report,
)
from intent_ledger.config import DATA_DIR
from intent_ledger.db import db, savepoint
from intent_ledger.importer.importer import import_statement
from intent_ledger.importer.uploads import INGEST_DIR, list_pending_statements

EXPORT_DIR = DATA_DIR / "exports"

log = logging.getLogger(__name__)


def initialize_database():
    db.initialize()


@dataclass
class LedgerChange:
    recategorized: int = 0


@contextmanager
def ledger_change():
    """Apply a change and rebuild the ledger from it in one transaction.

    If the change or the rebuild fails, both roll back, so the ledger can't
    fall out of step with the data it is built from.

    Yields:
        A LedgerChange whose `recategorized` count is set once the block ends.
    """
    change = LedgerChange()

    with db.transaction():
        yield change
        change.recategorized = rebuild_ledger()


def import_pending_statements() -> list[dict]:
    summaries = []

    with db.transaction() as conn:
        for entry in list_pending_statements():
            path = INGEST_DIR / entry["name"]

            try:
                if entry["account_id"] is None or entry["parser_slug"] is None:
                    raise ValueError("No account chosen for this statement.")
                with savepoint(conn):
                    summary = import_statement(
                        path, account_id=entry["account_id"], parser_slug=entry["parser_slug"]
                    )
            except Exception as e:
                log.exception("Could not import statement %s", entry["name"])
                error = str(e) or type(e).__name__
                summary = {"statement": str(path), "error": error, "inserted": 0, "row_errors": []}

            summaries.append(summary)

    return summaries


def export_all(accounts: bool = True, projects: bool = True, yearly: bool = True) -> list[Path]:
    exports = []

    if accounts:
        exports += [export_account_statement(account.id) for account in get_all_accounts()]

    if projects:
        exports += [export_project_report(project["id"]) for project in get_all_projects()]

    if yearly:
        exports += [export_yearly_report(year) for year in _years_with_transactions()]

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    paths = []

    for filename, content in exports:
        path = EXPORT_DIR / filename
        path.write_bytes(content)
        paths.append(path)

    return paths


def _years_with_transactions() -> list[int]:
    with db.transaction() as conn:
        rows = conn.execute("""
            SELECT DISTINCT CAST(strftime('%Y', posted_date) AS INTEGER) AS year
            FROM transactions
            ORDER BY year
        """).fetchall()

    return [row["year"] for row in rows]
