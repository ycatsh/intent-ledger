from calendar import month_name, monthrange

from flask import Blueprint, render_template, request

from intent_ledger.analytics.charts import cashflow_chart, expense_charts
from intent_ledger.analytics.reports import (
    budget_progress,
    get_categories,
    get_income_sources,
    get_payees,
    get_summary,
    monthly_income,
)
from intent_ledger.analytics.workbook import export_monthly_report, export_yearly_report
from intent_ledger.routes.downloads import send_export
from intent_ledger.routes.period import (
    get_period,
    period_links,
    requested_period,
    validate_date_range,
    validate_year,
)
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
        year, month = requested_period()
        start = f"{year}-{month:02d}-01"
        end = f"{year}-{month:02d}-{monthrange(year, month)[1]:02d}"
        period_label = f"{month_name[month]} {year}"

    return render_template(
        "expenses.html",
        mode=mode,
        nav=period_links("expenses.expenses", year, month) if mode == "month" else None,
        year=year,
        month=month,
        start=start,
        end=end,
        period_label=period_label,
        summary=get_summary(start=start, end=end),
        categories=get_categories(start=start, end=end),
        payees=get_payees(limit=10, start=start, end=end),
        inflows=get_income_sources(start=start, end=end),
        charts={
            "cashflow": cashflow_chart(),
            "progress": expense_charts(budget_progress(start, end), monthly_income())["burn"],
        },
    )


@expenses_bp.get("/expenses/export")
def expenses_export():
    mode = request.args.get("mode", "month")

    if mode == "year":
        year = request.args.get("year", type=int) or today().year
        validate_year(year)
        return send_export(export_yearly_report(year))

    year, month = get_period()
    return send_export(export_monthly_report(year, month))
