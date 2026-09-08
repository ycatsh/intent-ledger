from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from intent_ledger.db import db

# Matches the app_settings.timezone column default in schema.sql.
DEFAULT_TIMEZONE = "UTC"


def get_base_currency(conn) -> str:
    row = conn.execute("SELECT base_currency FROM app_settings WHERE id = 1").fetchone()

    return row["base_currency"]


def set_base_currency(conn, currency: str) -> None:
    currency = currency.strip()
    if not currency:
        raise ValueError("Currency is required.")

    conn.execute(
        "UPDATE app_settings SET base_currency = ? WHERE id = 1",
        (currency,),
    )


def get_export_format(conn) -> str:
    row = conn.execute("SELECT export_format FROM app_settings WHERE id = 1").fetchone()

    return row["export_format"]


def set_export_format(conn, export_format: str) -> None:
    export_format = (export_format or "").strip().lower()
    if export_format not in ("xlsx", "csv"):
        raise ValueError("Export format must be 'xlsx' or 'csv'.")

    conn.execute(
        "UPDATE app_settings SET export_format = ? WHERE id = 1",
        (export_format,),
    )


def get_timezone(conn) -> str:
    row = conn.execute("SELECT timezone FROM app_settings WHERE id = 1").fetchone()

    return row["timezone"]


def set_timezone(conn, timezone: str) -> None:
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        raise ValueError(f"Unknown timezone: {timezone}") from None

    conn.execute(
        "UPDATE app_settings SET timezone = ? WHERE id = 1",
        (timezone,),
    )


def today_in(conn) -> date:
    """Today's date in the app's configured timezone."""
    return datetime.now(ZoneInfo(get_timezone(conn))).date()


def today() -> date:
    with db.transaction() as conn:
        return today_in(conn)
