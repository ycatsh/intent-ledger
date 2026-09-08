from calendar import month_name, monthrange

from flask import Blueprint, redirect, render_template, request, send_file, session, url_for

from intent_ledger.analytics.charts import PALETTE, cashflow_chart, line_chart
from intent_ledger.analytics.reports import (
    budget_progress,
    get_category_summary,
    get_inflow_summary,
    get_monthly_summary,
    get_payee_summary,
)
from intent_ledger.analytics.workbook import export_monthly_report, export_yearly_report
from intent_ledger.routes.period import get_period, shift_period, validate_date_range, validate_year
from intent_ledger.settings import today

expenses_bp = Blueprint("expenses", __name__)


@expenses_bp.get("/expenses")
def expenses():
    mode = request.args.get("mode", "month")

    if mode == "year":
        year = request.args.get("year", type=int) or today().year
        validate_year(year)
        month = None
        start = f"{year}-01-01"
        end = f"{year}-12-31"
        period_label = str(year)
    elif mode == "range":
        start = request.args.get("start", "")
        end = request.args.get("end", "")
        if not start or not end:
            today_ = today()
            start = today_.replace(day=1).isoformat()
            end = today_.isoformat()
        else:
            validate_date_range(start, end)
        year = month = None
        period_label = f"{start} - {end}"
    else:
        mode = "month"
        year, month = get_period()
        start = f"{year}-{month:02d}-01"
        end = f"{year}-{month:02d}-{monthrange(year, month)[1]:02d}"
        period_label = f"{month_name[month]} {year}"

    progress = budget_progress(start, end)

    return render_template(
        "expenses.html",
        mode=mode,
        year=year,
        month=month,
        start=start,
        end=end,
        period_label=period_label,
        summary=get_monthly_summary(start=start, end=end),
        categories=get_category_summary(start=start, end=end),
        payees=get_payee_summary(start=start, end=end, limit=10),
        inflows=get_inflow_summary(start=start, end=end, limit=5),
        charts={
            "cashflow": cashflow_chart(),
            "progress": line_chart(
                progress["labels"],
                [
                    ("Spent", progress["spent"], PALETTE[3]),
                    ("Budget pace", progress["budget"], PALETTE[4]),
                ],
            ),
        },
    )


@expenses_bp.get("/expenses/export")
def expenses_export():
    mode = request.args.get("mode", "month")

    if mode == "year":
        year = request.args.get("year", type=int) or today().year
        validate_year(year)
        path = export_yearly_report(year)
    else:
        year, month = get_period()
        path = export_monthly_report(year, month)

    return send_file(path, as_attachment=True, download_name=path.name)


@expenses_bp.post("/expenses/current")
def current_period():
    session.pop("finance_year", None)
    session.pop("finance_month", None)
    return redirect(url_for("expenses.expenses"))


@expenses_bp.post("/expenses/previous")
def previous_period():
    shift_period(-1)
    return redirect(url_for("expenses.expenses"))


@expenses_bp.post("/expenses/next")
def next_period():
    shift_period(1)
    return redirect(url_for("expenses.expenses"))
