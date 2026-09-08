import pytest
from flask import Flask

from intent_ledger.routes.navigation import (
    active_tab,
    current_location,
    is_internal_path,
    redirect_back,
    return_target,
)


@pytest.fixture
def app():
    app = Flask(__name__)

    @app.get("/accounts")
    def accounts():
        return ""

    return app


@pytest.mark.parametrize(
    "target",
    ["/accounts", "/accounts?account_id=3&sort=amount_desc", "/rules?tab=overrides"],
)
def test_internal_paths_are_accepted(target):
    assert is_internal_path(target)


@pytest.mark.parametrize(
    "target",
    ["", None, "https://evil.example/x", "//evil.example/x", "javascript:alert(1)", "accounts"],
)
def test_anything_that_could_leave_the_app_is_rejected(target):
    assert not is_internal_path(target)


def test_return_target_reads_the_query_string_and_the_form(app):
    with app.test_request_context("/overrides/new?return_to=/accounts%3Faccount_id%3D3"):
        assert return_target() == "/accounts?account_id=3"

    with app.test_request_context("/overrides/new", method="POST", data={"return_to": "/accounts"}):
        assert return_target() == "/accounts"


def test_redirect_back_falls_back_to_the_owning_page(app):
    with app.test_request_context("/overrides/new", method="POST", data={"return_to": "/accounts?x=1"}):
        assert redirect_back("accounts").headers["Location"] == "/accounts?x=1"

    with app.test_request_context("/overrides/new", method="POST"):
        assert redirect_back("accounts").headers["Location"] == "/accounts"

    with app.test_request_context("/overrides/new", method="POST", data={"return_to": "http://evil/"}):
        assert redirect_back("accounts").headers["Location"] == "/accounts"


def test_current_location_keeps_the_query_string(app):
    with app.test_request_context("/accounts?account_id=3&sort=amount_desc"):
        assert current_location() == "/accounts?account_id=3&sort=amount_desc"

    with app.test_request_context("/accounts"):
        assert current_location() == "/accounts"


def test_active_tab_falls_back_when_the_tab_is_unknown(app):
    tabs = ("rules", "transfers", "overrides")

    with app.test_request_context("/rules?tab=overrides"):
        assert active_tab(tabs, "rules") == "overrides"

    with app.test_request_context("/rules?tab=nope"):
        assert active_tab(tabs, "rules") == "rules"

    with app.test_request_context("/rules"):
        assert active_tab(tabs, "rules") == "rules"
