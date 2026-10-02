from datetime import date, datetime

import pytest
from openpyxl import Workbook

from intent_ledger.importer.parsers.canonical import CanonicalParser


@pytest.fixture
def parser():
    return CanonicalParser()


def write_csv(path, text):
    path.write_text(text)
    return path


def test_parses_valid_rows(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit,balance\n"
        "2026-01-05,COFFEE SHOP,4.50,,995.50\n"
        "2026-01-06,PAYCHECK,,1500.00,2495.50\n",
    )

    result = parser.parse(path)

    assert result["row_errors"] == []
    assert len(result["transactions"]) == 2

    first = result["transactions"][0]
    assert first["posted_date"] == date(2026, 1, 5)
    assert first["amount_cents"] == -450
    assert first["balance_cents"] == 99550

    second = result["transactions"][1]
    assert second["amount_cents"] == 150000


def test_missing_required_column_raises(tmp_path, parser):
    path = write_csv(tmp_path / "statement.csv", "date,description\n2026-01-05,COFFEE\n")

    with pytest.raises(ValueError, match="Missing required column"):
        parser.parse(path)


def test_empty_file_raises(tmp_path, parser):
    path = write_csv(tmp_path / "statement.csv", "")

    with pytest.raises(ValueError, match="empty"):
        parser.parse(path)


def test_balance_column_is_optional(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit\n2026-01-05,COFFEE SHOP,4.50,\n",
    )

    result = parser.parse(path)

    assert result["row_errors"] == []
    assert result["transactions"][0]["balance_cents"] is None


def test_bad_date_is_a_row_error_not_a_hard_failure(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit,balance\n"
        "not-a-date,COFFEE SHOP,4.50,,995.50\n"
        "2026-01-06,PAYCHECK,,1500.00,2495.50\n",
    )

    result = parser.parse(path)

    assert len(result["transactions"]) == 1
    assert len(result["row_errors"]) == 1
    assert "row 2" in result["row_errors"][0]


def test_amount_with_too_many_decimal_places_is_a_row_error(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit,balance\n2026-01-05,COFFEE SHOP,4.505,,995.50\n",
    )

    result = parser.parse(path)

    assert result["transactions"] == []
    assert len(result["row_errors"]) == 1


def test_row_with_both_withdrawal_and_deposit_is_a_row_error(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit\n2026-01-05,COFFEE SHOP,4.50,1.00\n",
    )

    result = parser.parse(path)

    assert result["transactions"] == []
    assert len(result["row_errors"]) == 1
    assert "both" in result["row_errors"][0]


def test_row_with_neither_withdrawal_nor_deposit_is_a_row_error(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit\n2026-01-05,COFFEE SHOP,,\n",
    )

    result = parser.parse(path)

    assert result["transactions"] == []
    assert len(result["row_errors"]) == 1


def test_header_matching_is_case_insensitive(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "Date,Description,Withdrawal,Deposit,Balance\n2026-01-05,COFFEE SHOP,4.50,,995.50\n",
    )

    result = parser.parse(path)

    assert result["row_errors"] == []
    assert len(result["transactions"]) == 1


def test_blank_rows_are_skipped(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit,balance\n"
        "2026-01-05,COFFEE SHOP,4.50,,995.50\n"
        ",,,,\n"
        "2026-01-06,PAYCHECK,,1500.00,2495.50\n",
    )

    result = parser.parse(path)

    assert result["row_errors"] == []
    assert len(result["transactions"]) == 2


def test_sniff_true_for_matching_header(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit,balance\n2026-01-05,COFFEE SHOP,4.50,,995.50\n",
    )

    assert parser.sniff(path) is True


def test_sniff_false_for_missing_required_columns(tmp_path, parser):
    path = write_csv(tmp_path / "statement.csv", "foo,bar\n1,2\n")

    assert parser.sniff(path) is False


def test_a_negative_amount_is_a_row_error_not_a_flipped_sign(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit\n2026-01-05,REFUND?,-20.00,\n2026-01-06,TIP,,-3.00\n",
    )

    result = parser.parse(path)

    assert result["transactions"] == []
    assert len(result["row_errors"]) == 2


@pytest.mark.parametrize("cell", ["NaN", "inf", "1e3", "1,000.00"])
def test_an_unreadable_amount_is_a_row_error(tmp_path, parser, cell):
    path = write_csv(
        tmp_path / "statement.csv",
        f'date,description,withdrawal,deposit\n2026-01-05,COFFEE,"{cell}",\n2026-01-06,TEA,2.00,\n',
    )

    result = parser.parse(path)

    assert [t["raw_description"] for t in result["transactions"]] == ["TEA"]
    assert result["row_errors"][0].startswith("row 2:")


def test_every_date_in_a_file_is_read_with_one_format(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit\n03/04/2026,A,1.00,\n12/31/2026,B,1.00,\n",
    )

    result = parser.parse(path)

    assert [t["posted_date"] for t in result["transactions"]] == [date(2026, 3, 4), date(2026, 12, 31)]


def test_ambiguous_dates_read_day_first(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit\n03/04/2026,A,1.00,\n05/06/2026,B,1.00,\n",
    )

    result = parser.parse(path)

    assert [t["posted_date"] for t in result["transactions"]] == [date(2026, 4, 3), date(2026, 6, 5)]


def test_a_date_in_another_format_than_the_rest_is_a_row_error(tmp_path, parser):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit\n2026-01-05,A,1.00,\n2026-01-06,B,1.00,\n07/01/2026,C,1.00,\n",
    )

    result = parser.parse(path)

    assert [t["raw_description"] for t in result["transactions"]] == ["A", "B"]
    assert result["row_errors"][0].startswith("row 4:")


def test_xlsx_date_cells_are_read_as_dates(tmp_path, parser):
    workbook = Workbook()
    workbook.active.append(["date", "description", "withdrawal", "deposit", "balance"])
    workbook.active.append([datetime(2026, 1, 5), "COFFEE SHOP", 4.5, None, 995.5])
    workbook.active.append([date(2026, 1, 6), "PAYCHECK", None, 1500, 2495.5])
    path = tmp_path / "statement.xlsx"
    workbook.save(path)

    result = parser.parse(path)

    assert result["row_errors"] == []
    assert [(t["posted_date"], t["amount_cents"]) for t in result["transactions"]] == [
        (date(2026, 1, 5), -450),
        (date(2026, 1, 6), 150000),
    ]
