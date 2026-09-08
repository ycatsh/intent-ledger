import pytest

from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.projects import create_project
from intent_ledger.analytics import workbook
from intent_ledger.service import export_all


class FakeForm(dict):
    def get(self, key, default=None, type=None):
        if key not in self:
            return default
        value = super().get(key)
        if type is None:
            return value
        try:
            return type(value)
        except (TypeError, ValueError):
            return default


@pytest.fixture(autouse=True)
def redirect_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(workbook, "FINANCE_EXPORT_DIR", tmp_path / "exports")


def test_export_all_covers_every_account_and_project(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    transaction_factory(checking_id, "2026-01-05", -1500, "GROCERY RUN")
    rebuild_ledger()
    create_project(FakeForm({"name": "Test Project"}))

    paths = export_all()

    # One statement per account (including the seeded system accounts) plus
    # one project report plus one yearly report for 2026.
    assert sum(p.name.startswith("statement_") for p in paths) >= 1
    assert any(p.name == "project-report_test-project.xlsx" for p in paths)
    assert any(p.name == "exp-report_2026.xlsx" for p in paths)
    assert all(p.exists() for p in paths)


def test_export_all_respects_category_toggles(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    transaction_factory(checking_id, "2026-01-05", -1500, "GROCERY RUN")
    rebuild_ledger()
    create_project(FakeForm({"name": "Test Project"}))

    paths = export_all(accounts=False, projects=False, yearly=True)

    assert all(p.name == "exp-report_2026.xlsx" for p in paths)
    assert len(paths) == 1


def test_export_all_returns_nothing_when_no_data(conn):
    assert export_all(accounts=False, projects=False, yearly=True) == []
