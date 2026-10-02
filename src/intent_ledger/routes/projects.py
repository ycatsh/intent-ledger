from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from intent_ledger.accounting.projects import (
    create_project,
    delete_project,
    get_project,
    get_project_trend,
    get_projects_overview,
)
from intent_ledger.analytics.charts import project_charts
from intent_ledger.analytics.reports import (
    get_categories,
    get_income_expense_lines,
    get_payees,
    get_summary,
)
from intent_ledger.analytics.workbook import export_project_report
from intent_ledger.domain.money import Money
from intent_ledger.routes.downloads import send_export

projects_bp = Blueprint("projects", __name__)


@projects_bp.get("/projects")
def projects():
    project_id = request.args.get("project_id", type=int)

    overview = get_projects_overview()

    if project_id is None and overview:
        project_id = overview[0]["id"]

    project = get_project(project_id) if project_id else None
    summary = get_summary(project_id=project_id) if project else None
    budget_amount = Money(project["budget_cents"]).amount if project and project["budget_cents"] else None
    budget_percent = (
        round(100 * summary["expenses"] / budget_amount, 1) if budget_amount and summary else None
    )
    lines = get_income_expense_lines(project_id=project_id) if project else []

    return render_template(
        "projects.html",
        projects=overview,
        selected_project_id=project_id,
        project=project,
        summary=summary,
        budget_amount=budget_amount,
        budget_percent=budget_percent,
        categories=get_categories(project_id=project_id) if project else [],
        transactions=[
            {**line, "amount": Money(line["amount_cents"] * (1 if line["type"] == "income" else -1)).amount}
            for line in lines
        ],
        payees=get_payees(limit=10, project_id=project_id) if project else [],
        charts=project_charts(get_project_trend(project_id), budget_amount) if project else {},
    )


@projects_bp.get("/projects/export")
def projects_export():
    project_id = request.args.get("project_id", type=int)
    if project_id is None:
        abort(404)

    try:
        export = export_project_report(project_id)
    except ValueError:
        abort(404)

    return send_export(export)


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
