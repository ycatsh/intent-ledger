from zoneinfo import available_timezones

from flask import Blueprint, flash, redirect, render_template, request, url_for

from intent_ledger.db import db
from intent_ledger.settings import (
    DEFAULT_TIMEZONE,
    get_base_currency,
    get_export_format,
    get_timezone,
    set_base_currency,
    set_export_format,
    set_timezone,
)

settings_bp = Blueprint("settings", __name__)


@settings_bp.get("/settings")
def settings():
    with db.transaction() as conn:
        timezone = get_timezone(conn)
        base_currency = get_base_currency(conn)
        export_format = get_export_format(conn)

    return render_template(
        "settings.html",
        timezone=timezone,
        timezone_options=sorted(available_timezones()),
        timezone_is_default=timezone == DEFAULT_TIMEZONE,
        base_currency=base_currency,
        export_format=export_format,
    )


@settings_bp.post("/settings")
def settings_save():
    timezone = request.form.get("timezone", "")
    base_currency = request.form.get("base_currency", "")
    export_format = request.form.get("export_format", "")

    try:
        with db.transaction() as conn:
            set_timezone(conn, timezone)
            set_base_currency(conn, base_currency)
            set_export_format(conn, export_format)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("settings.settings"))

    flash("Settings saved.", "success")
    return redirect(url_for("settings.settings"))
