from flask import Blueprint, render_template

from intent_ledger.analytics.charts import get_charts_page

charts_bp = Blueprint("charts", __name__)


@charts_bp.get("/charts")
def charts():
    return render_template("charts.html", charts=get_charts_page())
