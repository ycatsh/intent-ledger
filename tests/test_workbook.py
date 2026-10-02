import csv
import io
import zipfile
from datetime import datetime

import pytest
from openpyxl import load_workbook

from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.analytics import reports, workbook
from intent_ledger.settings import set_export_format, today

JANUARY = {"year": 2026, "month": 1}


@pytest.fixture
def world(conn, account_factory, transaction_factory):
    ids = {
        "checking": account_factory("Test Checking"),
        "savings": account_factory("Test Savings"),
        "card": account_factory("Test Card", type="liability"),
        "food": account_factory("Test Food", type="expense"),
        "salary": account_factory("Test Salary", type="income"),
        "opening": account_factory("Test Opening", type="equity"),
    }
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id) VALUES ('contains', 'ACME', ?)",
        (ids["salary"],),
    )

    def pinned(account, day, cents, description, category):
        transaction_hash = transaction_factory(account, day, cents, description)
        conn.execute(
            "INSERT INTO transactions_overrides (transaction_hash, account_id) VALUES (?, ?)",
            (transaction_hash, category),
        )
        return transaction_hash

    pinned(ids["checking"], "2026-01-01", 100000, "OPENING BALANCE", ids["opening"])
    transaction_factory(ids["checking"], "2026-01-05", 50000, "ACME PAYROLL")
    pinned(ids["card"], "2026-01-06", -3000, "CAFE", ids["food"])
    pinned(ids["card"], "2026-01-07", 500, "CAFE REFUND", ids["food"])
    pinned(ids["checking"], "2026-01-08", -20000, "TO SAVINGS", ids["savings"])
    alice = pinned(ids["checking"], "2026-01-09", -1500, "LUNCH WITH ALICE", ids["food"])
    counterparty = conn.execute("INSERT INTO counterparties (name) VALUES ('Alice')").lastrowid
    conn.execute(
        "UPDATE transactions SET counterparty_id = ? WHERE transaction_hash = ?", (counterparty, alice)
    )
    project = conn.execute("INSERT INTO projects (name, budget_cents) VALUES ('Party', 10000)").lastrowid
    conn.execute(
        "INSERT INTO transactions_projects (transaction_hash, project_id) VALUES (?, ?)", (alice, project)
    )
    conn.commit()
    rebuild_ledger()
    return {**ids, "alice": alice, "project": project}


def _open(export):
    return load_workbook(io.BytesIO(export[1]))


def _labels(sheet, label=1, value=2):
    return {
        row[label - 1].value: row[value - 1].value
        for row in sheet.iter_rows()
        if isinstance(row[label - 1].value, str) and len(row) >= value
    }


def _total_row(sheet):
    return next(row for row in sheet.iter_rows(values_only=True) if row[0] == "Total")


def _grand_total(sheet):
    return next(row[2] for row in sheet.iter_rows(values_only=True) if row[0] == "Grand total")


def _csv(export, name):
    with zipfile.ZipFile(io.BytesIO(export[1])) as archive:
        return list(csv.reader(archive.read(f"{name}.csv").decode().splitlines()))


def test_the_summary_is_net_and_leaves_out_own_transfers(world):
    summary = reports.get_summary(**JANUARY)

    assert summary["income_cents"] == 50000
    assert summary["expenses_cents"] == 3000 - 500 + 1500
    assert summary["net_cents"] == 50000 - 4000
    assert summary["inflow_cents"] == 100000 + 50000 + 500
    assert summary["outflow_cents"] == 3000 + 1500
    assert summary["transactions"] == 6


def test_breakdowns_add_up_to_the_summary(world):
    expenses = reports.get_summary(**JANUARY)["expenses_cents"]

    assert sum(r["amount_cents"] for r in reports.get_categories(**JANUARY)) == expenses
    assert sum(r["amount_cents"] for r in reports.get_payees(**JANUARY)) == expenses


def test_income_sources_list_income_first(world):
    sources = reports.get_income_sources(**JANUARY)

    assert [(r["account"], r["kind"], r["amount_cents"]) for r in sources] == [
        ("Test Salary", "Income", 50000),
        ("Test Opening", "Equity", 100000),
        ("Test Food", "Refund", 500),
    ]


def test_the_income_and_expense_report_reconciles(world):
    book = _open(workbook.export_monthly_report(2026, 1))

    summary = _labels(book["Summary"])
    assert (summary["Income"], summary["Expenses"], summary["Net"]) == (500.0, 40.0, 460.0)
    assert (summary["Money in"], summary["Money out"], summary["Cash flow"]) == (1505.0, 45.0, 1460.0)

    total = _total_row(book["Transactions"])
    assert (total[4], total[5]) == (500.0, 40.0)
    assert _total_row(book["Categories"])[1] == 40.0
    assert _total_row(book["Income sources"])[3] == 1505.0

    first = next(book["Transactions"].iter_rows(min_row=2, values_only=True))
    assert isinstance(first[0], datetime)


def test_a_statement_reconciles_opening_to_closing(world):
    book = _open(workbook.export_account_statement(world["checking"]))

    summary = _labels(book["Summary"])
    assert summary["Opening balance"] == 0
    assert (summary["Money in"], summary["Money out"]) == (1500.0, 215.0)
    assert summary["Closing balance"] == 1285.0

    rows = [r for r in book["Transactions"].iter_rows(min_row=2, values_only=True) if r[0] != "Total"]
    assert rows[-1][7] == 1285.0
    assert {row[2]: row[3] for row in rows}["Test Savings"] == "Asset"
    assert _total_row(book["Transactions"])[5:7] == (1500.0, 215.0)


def test_by_category_groups_payees_under_each_category_with_subtotals(world):
    sheet = _open(workbook.export_account_statement(world["checking"]))["By category"]
    rows = list(sheet.iter_rows(min_row=2, values_only=True))

    headers = [row[0] for row in rows if row[0] and row[0] != "Grand total"]
    subtotals = [row[2] for row in rows if row[1] == "Total"]

    assert headers == [
        "Test Food (expense)",
        "Test Opening (equity)",
        "Test Salary (income)",
        "Test Savings (asset)",
    ]
    assert subtotals == [-15.0, 1000.0, 500.0, -200.0]
    assert _grand_total(sheet) == sum(subtotals) == 1285.0


def test_a_filtered_statement_drops_the_opening_and_closing_balance(world):
    summary = _labels(_open(workbook.export_account_statement(world["checking"], search="acme"))["Summary"])

    assert "Opening balance" not in summary
    assert summary["Filter"] == "acme"


def test_a_split_gets_one_highlighted_row_per_part_and_still_reconciles(conn, world, account_factory):
    gifts = account_factory("Test Gifts", type="expense")
    conn.execute("DELETE FROM transactions_overrides WHERE transaction_hash = ?", (world["alice"],))
    conn.executemany(
        """
        INSERT INTO transactions_splits (transaction_hash, account_id, amount_cents, note)
        VALUES (?, ?, ?, ?)
        """,
        [(world["alice"], world["food"], 1000, "Lunch"), (world["alice"], gifts, 500, None)],
    )
    conn.commit()
    rebuild_ledger()

    book = _open(workbook.export_account_statement(world["checking"]))
    sheet = book["Transactions"]
    split = [r for r in sheet.iter_rows(min_row=2) if r[0].fill.start_color.rgb == "00FBF3DB"]

    parts = [(r[2].value, r[4].value, r[6].value, r[7].value) for r in split]
    assert parts == [("Test Food", "Lunch", 10.0, None), ("Test Gifts", "LUNCH WITH ALICE", 5.0, 1285.0)]
    assert _labels(book["Summary"])["Closing balance"] == 1285.0


def test_the_balance_sheet_matches_net_worth_and_its_memos_match_their_detail(world):
    book = _open(workbook.export_balance_sheet())
    sheet = book["Balance sheet"]

    left = _labels(sheet)
    assert left["Total assets"] == 1485.0
    assert left["Total liabilities"] == 25.0
    assert left["Net worth"] == 1460.0

    memo = _labels(sheet, label=4, value=5)
    assert memo["Test Opening"] == 1000.0
    assert memo["Alice"] == 15.0
    assert _total_row(book["Equity detail"])[5] == 1000.0
    assert _total_row(book["Counter-party detail"])[6] == 15.0


def test_the_project_report_covers_only_the_project(world):
    book = _open(workbook.export_project_report(world["project"]))

    summary = _labels(book["Summary"])
    assert (summary["Expenses"], summary["Budget left"]) == (15.0, 85.0)
    assert summary["Budget used %"] == 15.0
    assert _total_row(book["Transactions"])[5] == 15.0


def test_file_names_start_with_their_period(world):
    month = today().strftime("%Y-%m")

    assert workbook.export_monthly_report(2026, 1)[0] == "2026-01_income_expenses.xlsx"
    assert workbook.export_yearly_report(2026)[0] == "2026_income_expenses.xlsx"
    assert workbook.export_account_statement(world["checking"])[0] == "2026-01_statement_test_checking.xlsx"
    assert workbook.export_balance_sheet()[0] == f"{month}_balance_sheet.xlsx"
    assert workbook.export_project_report(world["project"])[0] == f"{month}_project_party.xlsx"


def test_every_transaction_sheet_keeps_the_description_column_hidden(world):
    for export in (
        workbook.export_monthly_report(2026, 1),
        workbook.export_project_report(world["project"]),
        workbook.export_account_statement(world["checking"]),
    ):
        sheet = _open(export)["Transactions"]
        column = next(c.column_letter for c in sheet[1] if c.value == "Description")
        assert sheet.column_dimensions[column].hidden, export[0]


def test_bank_text_that_looks_like_a_formula_stays_text(world, transaction_factory):
    transaction_factory(world["checking"], "2026-01-20", -100, '=HYPERLINK("http://example.invalid","x")')
    rebuild_ledger()

    rows = _open(workbook.export_account_statement(world["checking"]))["Transactions"].iter_rows(min_row=2)
    cell = next(r[4] for r in rows if (r[4].value or "").startswith("="))

    assert cell.data_type == "s"


def test_csv_exports_zip_one_file_per_sheet(conn, world):
    set_export_format(conn, "csv")
    conn.commit()

    export = workbook.export_monthly_report(2026, 1)

    assert export[0] == "2026-01_income_expenses.zip"
    with zipfile.ZipFile(io.BytesIO(export[1])) as archive:
        assert set(archive.namelist()) == {
            "summary.csv",
            "categories.csv",
            "payees.csv",
            "income_sources.csv",
            "transactions.csv",
        }
    assert ["Test Food", "40.0", "3", "100.0"] in _csv(export, "categories")


def test_unknown_accounts_and_projects_raise(world):
    with pytest.raises(ValueError):
        workbook.export_account_statement(99999)
    with pytest.raises(ValueError):
        workbook.export_project_report(99999)
