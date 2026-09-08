from datetime import date

from flask import abort, session

from intent_ledger.accounting.budget import shift_year_month
from intent_ledger.settings import today


def validate_month(month: int):
    if not isinstance(month, int) or not 1 <= month <= 12:
        abort(404)


def validate_year(year: int):
    if not isinstance(year, int) or not 2000 <= year <= 2200:
        abort(404)


def validate_date_range(start: str, end: str):
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError:
        abort(404)

    if start_date > end_date:
        abort(404)


def get_period():
    if "finance_year" in session and "finance_month" in session:
        year = session["finance_year"]
        month = session["finance_month"]
    else:
        today_ = today()
        year, month = today_.year, today_.month

    validate_year(year)
    validate_month(month)

    return year, month


def set_period(year: int, month: int):
    validate_year(year)
    validate_month(month)

    session["finance_year"] = year
    session["finance_month"] = month


def shift_period(delta: int):
    year, month = get_period()
    year, month = shift_year_month(year, month, delta)
    set_period(year, month)
