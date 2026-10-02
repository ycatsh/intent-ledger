import json
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from intent_ledger import service
from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.projects import create_project
from intent_ledger.accounting.rules import add_account_rule
from intent_ledger.importer import uploads
from intent_ledger.service import export_all, import_pending_statements, ledger_change
from intent_ledger.settings import today


class FakeForm(MultiDict):
    def __init__(self, values=None, **fields):
        fields = {**(values or {}), **fields}
        super().__init__(
            [
                (key, str(item))
                for key, value in fields.items()
                for item in (value if isinstance(value, list) else [value])
                if item is not None
            ]
        )


@pytest.fixture(autouse=True)
def redirect_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "EXPORT_DIR", tmp_path / "exports")


def test_export_all_covers_every_account_and_project(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    transaction_factory(checking_id, "2026-01-05", -1500, "GROCERY RUN")
    rebuild_ledger()
    create_project(FakeForm({"name": "Test Project"}))

    paths = export_all()

    names = {p.name for p in paths}
    assert "2026-01_statement_test_checking.xlsx" in names
    assert f"{today():%Y-%m}_project_test_project.xlsx" in names
    assert "2026_income_expenses.xlsx" in names
    assert all(p.exists() for p in paths)


def test_export_all_respects_category_toggles(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    transaction_factory(checking_id, "2026-01-05", -1500, "GROCERY RUN")
    rebuild_ledger()
    create_project(FakeForm({"name": "Test Project"}))

    paths = export_all(accounts=False, projects=False, yearly=True)

    assert [p.name for p in paths] == ["2026_income_expenses.xlsx"]


def test_export_all_returns_nothing_when_no_data(conn):
    assert export_all(accounts=False, projects=False, yearly=True) == []


def test_a_failed_rebuild_rolls_back_the_change_that_caused_it(conn, account_factory, monkeypatch):
    groceries = account_factory("Test Groceries", type="expense")

    def broken_rebuild():
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(service, "rebuild_ledger", broken_rebuild)

    with pytest.raises(RuntimeError), ledger_change():
        add_account_rule(FakeForm(match_type="contains", pattern="COFFEE", account_id=groceries))

    assert conn.execute("SELECT COUNT(*) AS n FROM account_rules").fetchone()["n"] == 0


def test_a_ledger_change_reports_how_many_transactions_moved(conn, account_factory, transaction_factory):
    checking = account_factory("Test Checking")
    groceries = account_factory("Test Groceries", type="expense")
    transaction_factory(checking, "2026-01-05", -450, "COFFEE SHOP")
    transaction_factory(checking, "2026-01-06", -900, "BOOKSTORE")
    rebuild_ledger()

    with ledger_change() as change:
        add_account_rule(FakeForm(match_type="contains", pattern="COFFEE", account_id=groceries))

    assert change.recategorized == 1


def test_a_bad_statement_is_reported_and_the_others_still_import(
    conn, account_factory, tmp_path, monkeypatch
):
    checking = account_factory("Test Checking")
    ingest = tmp_path / "ingest" / "pending"
    ingest.mkdir(parents=True)
    monkeypatch.setattr(uploads, "INGEST_DIR", ingest)
    monkeypatch.setattr(uploads, "MANIFEST_PATH", ingest.parent / "manifest.json")
    monkeypatch.setattr(service, "INGEST_DIR", ingest)

    (ingest / "good.csv").write_text("date,description,withdrawal,deposit\n2026-01-05,COFFEE,4.50,\n")
    (ingest / "bad.csv").write_text("date,description\n2026-01-05,COFFEE\n")
    (ingest / "orphan.csv").write_text("date,description,withdrawal,deposit\n2026-01-06,TEA,2.00,\n")
    entry = {"uploaded_at": 0, "source": "test", "account_id": checking, "parser_slug": "canonical"}
    uploads.MANIFEST_PATH.write_text(json.dumps({"good.csv": entry, "bad.csv": entry}))

    with ledger_change():
        summaries = {Path(s["statement"]).name: s for s in import_pending_statements()}

    assert summaries["good.csv"]["inserted"] == 1
    assert "Missing required column" in summaries["bad.csv"]["error"]
    assert summaries["orphan.csv"]["error"] == "No account chosen for this statement."
    assert conn.execute("SELECT COUNT(*) AS n FROM ledger").fetchone()["n"] == 2
