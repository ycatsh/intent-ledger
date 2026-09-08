from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from intent_ledger.settings import (
    get_base_currency,
    get_export_format,
    get_timezone,
    set_base_currency,
    set_export_format,
    set_timezone,
    today_in,
)


def test_timezone_defaults_to_utc(conn):
    assert get_timezone(conn) == "UTC"


def test_set_timezone_persists(conn):
    set_timezone(conn, "America/New_York")
    conn.commit()

    assert get_timezone(conn) == "America/New_York"


def test_set_timezone_rejects_unknown_zones(conn):
    with pytest.raises(ValueError, match="Unknown timezone"):
        set_timezone(conn, "Not/A_Real_Zone")

    assert get_timezone(conn) == "UTC"


def test_today_in_uses_the_configured_timezone(conn):
    set_timezone(conn, "Pacific/Kiritimati")
    conn.commit()

    assert today_in(conn) == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()


def test_base_currency_defaults_to_usd(conn):
    assert get_base_currency(conn) == "USD"


def test_set_base_currency_persists(conn):
    set_base_currency(conn, "EUR")
    conn.commit()

    assert get_base_currency(conn) == "EUR"


def test_set_base_currency_accepts_any_non_empty_label(conn):
    set_base_currency(conn, "BTC")
    conn.commit()

    assert get_base_currency(conn) == "BTC"


def test_set_base_currency_rejects_blank(conn):
    with pytest.raises(ValueError, match="required"):
        set_base_currency(conn, "   ")

    assert get_base_currency(conn) == "USD"


def test_export_format_defaults_to_xlsx(conn):
    assert get_export_format(conn) == "xlsx"


def test_set_export_format_persists(conn):
    set_export_format(conn, "csv")
    conn.commit()

    assert get_export_format(conn) == "csv"


def test_set_export_format_is_case_insensitive(conn):
    set_export_format(conn, "CSV")
    conn.commit()

    assert get_export_format(conn) == "csv"


def test_set_export_format_rejects_unknown_formats(conn):
    with pytest.raises(ValueError, match=r"xlsx.*csv"):
        set_export_format(conn, "pdf")

    assert get_export_format(conn) == "xlsx"


@pytest.fixture
def client(conn):
    from intent_ledger import create_app

    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    with app.test_client() as test_client:
        yield test_client


def test_settings_page_renders(client):
    response = client.get("/settings")

    assert response.status_code == 200
    assert b"UTC" in response.data


def test_settings_post_saves_timezone_and_currency(client, conn):
    response = client.post(
        "/settings",
        data={"timezone": "Europe/London", "base_currency": "GBP", "export_format": "csv"},
    )

    assert response.status_code == 302
    assert get_timezone(conn) == "Europe/London"
    assert get_base_currency(conn) == "GBP"
    assert get_export_format(conn) == "csv"


def test_settings_post_rejects_unknown_timezone(client, conn):
    client.post("/settings", data={"timezone": "Not/Real", "base_currency": "USD"})

    assert get_timezone(conn) == "UTC"


def test_settings_post_rejects_blank_currency(client, conn):
    client.post("/settings", data={"timezone": "UTC", "base_currency": "  "})

    assert get_base_currency(conn) == "USD"


def test_settings_page_offers_browser_timezone_until_one_is_chosen(client, conn):
    assert b"data-detect-timezone>" in client.get("/settings").data

    set_timezone(conn, "Europe/London")
    conn.commit()

    assert b"data-detect-timezone>" not in client.get("/settings").data
