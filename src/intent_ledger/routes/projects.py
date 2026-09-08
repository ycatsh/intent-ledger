from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from intent_ledger.accounting.projects import (
    create_project,
    delete_project,
    get_project,
    get_project_category_summary,
    get_project_inflow_summary,
    get_project_payee_summary,
    get_project_summary,
    get_project_transactions,
    get_project_trend,
    get_projects_overview,
)
from intent_ledger.analytics.charts import PALETTE, line_chart
from intent_ledger.analytics.workbook import export_project_report
from intent_ledger.domain.money import Money

projects_bp = Blueprint("projects", __name__)


@projects_bp.get("/projects")
def projects():
    project_id = request.args.get("project_id", type=int)

    overview = get_projects_overview()

    if project_id is None and overview:
        project_id = overview[0]["id"]

    project = get_project(project_id) if project_id else None
    summary = get_project_summary(project_id) if project_id else None
    budget_amount = Money(project["budget_cents"]).amount if project and project["budget_cents"] else None
    budget_percent = (
        round(100 * summary["expenses"] / budget_amount, 1) if budget_amount and summary else None
    )

    categories = get_project_category_summary(project_id) if project_id else []
    trend = (
        get_project_trend(project_id)
        if project_id
        else {
            "labels": [],
            "daily_expenses": [],
            "daily_income": [],
            "cumulative_expenses": [],
            "cumulative_income": [],
            "has_income": False,
        }
    )

    trend_series = [("Cumulative expenses", trend["cumulative_expenses"], PALETTE[3])]
    if trend["has_income"]:
        trend_series.append(("Cumulative income", trend["cumulative_income"], PALETTE[1]))

    charts = {
        "trend": line_chart(trend["labels"], trend_series),
        "daily": {
            "labels": trend["labels"],
            "datasets": [
                {
                    "label": "Daily spend",
                    "data": [round(v, 2) for v in trend["daily_expenses"]],
                    "color": PALETTE[3],
                }
            ],
        },
    }

    return render_template(
        "projects.html",
        projects=overview,
        selected_project_id=project_id,
        project=project,
        summary=summary,
        budget_amount=budget_amount,
        budget_percent=budget_percent,
        categories=categories,
        transactions=get_project_transactions(project_id) if project_id else [],
        payees=get_project_payee_summary(project_id, limit=10) if project_id else [],
        inflows=get_project_inflow_summary(project_id, limit=10) if project_id else [],
        charts=charts,
    )


@projects_bp.get("/projects/export")
def projects_export():
    project_id = request.args.get("project_id", type=int)
    if project_id is None:
        abort(404)

    try:
        path = export_project_report(project_id)
    except ValueError:
        abort(404)

    return send_file(path, as_attachment=True, download_name=path.name)


@projects_bp.post("/projects/new")
def projects_new():
    try:
        project_id = create_project(request.form)
        flash("Project created.", "success")
    except ValueError as e:
        flash(f"{e}", "error")
        return redirect(url_for("projects.projects"))

    return redirect(url_for("projects.projects", project_id=project_id))


@projects_bp.post("/projects/<int:project_id>/delete")
def projects_delete(project_id):
    delete_project(project_id)
    flash("Project deleted.", "info")
    return redirect(url_for("projects.projects"))
