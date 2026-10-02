from io import BytesIO

import pytest
from werkzeug.middleware.proxy_fix import ProxyFix

from intent_ledger import config, create_app, service
from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.db import db
from intent_ledger.importer import uploads

MISSING = 99999


def form(build):
    return "form", build


def json_body(build):
    return "json", build


def upload(**fields):
    statement = b"date,description,withdrawal,deposit\n2026-01-05,COFFEE,1.00,\n"
    return form(lambda i: {"statements": (BytesIO(statement), "s.csv"), **fields})


MALFORMED = {
    "accounts.accounts_new": [
        ("/accounts/new", form(lambda i: {"name": "", "type": "asset"})),
        ("/accounts/new", form(lambda i: {"name": "Test Savings", "type": "equity"})),
        ("/accounts/new", form(lambda i: {"name": "test checking", "type": "asset"})),
    ],
    "accounts.assign_counterparty_route": [
        (
            "/accounts/assign-counterparty",
            form(lambda i: {"ids": i["bank_hash"], "counterparty_name": "Nobody"}),
        ),
        ("/accounts/assign-counterparty", form(lambda i: {"counterparty_name": ""})),
    ],
    "accounts.assign_project": [
        ("/accounts/assign-project", form(lambda i: {"ids": i["bank_hash"], "project_name": "Nope"})),
        ("/accounts/assign-project", form(lambda i: {"ids": "nope", "project_name": "Test Project"})),
        ("/accounts/assign-project", form(lambda i: {"project_name": "Test Project"})),
    ],
    "accounts.transactions_delete": [
        ("/transactions/nope/delete", form(lambda i: {})),
        ("/transactions/{bank_hash}/delete", form(lambda i: {})),
    ],
    "accounts.transactions_new": [
        ("/transactions/new", form(lambda i: {**i["manual"], "from_account_id": "x"})),
        (
            "/transactions/new",
            form(lambda i: {**i["manual"], "from_account_id": MISSING, "payee_name": "Shop"}),
        ),
        ("/transactions/new", form(lambda i: {**i["manual"], "category_account_id": MISSING})),
        ("/transactions/new", form(lambda i: {**i["manual"], "posted_date": "2026-02-30"})),
        ("/transactions/new", form(lambda i: {**i["manual"], "amount": "1,000"})),
        ("/transactions/new", form(lambda i: {**i["manual"], "amount": "0"})),
    ],
    "accounts.unassign_project": [
        ("/accounts/unassign-project", form(lambda i: {"ids": i["bank_hash"], "project_name": "Nope"})),
    ],
    "budget.budget_post": [
        ("/budget", form(lambda i: {"action": "save", f"budget_{i['groceries']}": "1,000"})),
        ("/budget", form(lambda i: {"action": "save", f"budget_{i['groceries']}": "-5"})),
        ("/budget", form(lambda i: {"action": "save_goal:x", "goal_amount_x": "5"})),
        (
            "/budget",
            form(lambda i: {"action": f"save_goal:{i['groceries']}", f"goal_amount_{i['groceries']}": "a"}),
        ),
        (
            "/budget",
            form(
                lambda i: {
                    "action": f"save_goal:{i['groceries']}",
                    f"goal_amount_{i['groceries']}": "5",
                    f"goal_date_{i['groceries']}": "soon",
                }
            ),
        ),
        (
            "/budget",
            form(
                lambda i: {
                    "action": f"move_budget:{i['groceries']}",
                    f"move_to_{i['groceries']}": i["checking"],
                    f"move_amount_{i['groceries']}": "1",
                }
            ),
        ),
        (
            "/budget",
            form(
                lambda i: {
                    "action": f"move_budget:{i['groceries']}",
                    f"move_to_{i['groceries']}": i["dining"],
                    f"move_amount_{i['groceries']}": "-1",
                }
            ),
        ),
    ],
    "dashboard.refresh": [],
    "expenses.current_period": [],
    "expenses.next_period": [],
    "expenses.previous_period": [],
    "import.import_delete": [("/import/nope.csv/delete", form(lambda i: {}))],
    "import.import_set_parser": [
        ("/import/{checking}/parser", json_body(lambda i: [])),
        ("/import/{checking}/parser", json_body(lambda i: {"parser_slug": ["canonical"]})),
        ("/import/{checking}/parser", json_body(lambda i: {"parser_slug": "nope"})),
    ],
    "import.import_upload": [
        ("/import/upload", upload(account_id="x", parser_slug="canonical")),
        ("/import/upload", upload(account_id=MISSING, parser_slug="canonical")),
        ("/import/upload", upload(account_id="{groceries}", parser_slug="canonical")),
        ("/import/upload", upload(account_id="{checking}", parser_slug="nope")),
    ],
    "inbox.inbox_assign_payee": [
        ("/assign", form(lambda i: {"ids": "x"})),
        ("/assign", form(lambda i: {"ids": "1", "members_1": "x", "account_1": "Test Groceries"})),
        ("/assign", form(lambda i: {"ids": "1", "members_1": MISSING, "account_1": "Test Groceries"})),
        ("/assign", form(lambda i: {"ids": "1", "members_1": i["bank_id"], "account_1": ""})),
    ],
    "mappings.mappings_ignore_account": [(f"/mappings/accounts/{MISSING}/ignore", form(lambda i: {}))],
    "mappings.mappings_save": [
        ("/mappings/save", json_body(lambda i: [])),
        ("/mappings/save", json_body(lambda i: {"changes": "all"})),
        (
            "/mappings/save",
            json_body(lambda i: {"changes": [{"op": "delete", "table": "accounts", "id": i["checking"]}]}),
        ),
        (
            "/mappings/save",
            json_body(
                lambda i: {
                    "changes": [
                        {"op": "update", "table": "accounts", "id": i["unknown"], "fields": {"type": "asset"}}
                    ]
                }
            ),
        ),
        (
            "/mappings/save",
            json_body(
                lambda i: {
                    "changes": [
                        {
                            "op": "update",
                            "table": "accounts",
                            "id": i["checking"],
                            "fields": {"type": "bogus"},
                        }
                    ]
                }
            ),
        ),
    ],
    "mappings.payees_quick_create": [
        ("/payees/quick-create", json_body(lambda i: [])),
        ("/payees/quick-create", json_body(lambda i: {"name": 5})),
        ("/payees/quick-create", json_body(lambda i: {"name": "Shop", "account_id": "x"})),
        ("/payees/quick-create", json_body(lambda i: {"name": "Shop", "account_id": MISSING})),
    ],
    "projects.projects_delete": [(f"/projects/{MISSING}/delete", form(lambda i: {}))],
    "projects.projects_new": [
        ("/projects/new", form(lambda i: {"name": ""})),
        ("/projects/new", form(lambda i: {"name": "Test Project"})),
        ("/projects/new", form(lambda i: {"name": "New", "budget": "abc"})),
        ("/projects/new", form(lambda i: {"name": "New", "start_date": "soon"})),
    ],
    "rules.overrides_add": [
        ("/overrides/new", form(lambda i: {"transaction_hash": "nope", "account_id": i["groceries"]})),
        ("/overrides/new", form(lambda i: {"transaction_hash": i["bank_hash"], "account_id": "x"})),
        ("/overrides/new", form(lambda i: {"transaction_hash": i["bank_hash"], "account_id": MISSING})),
        (
            "/overrides/new",
            form(
                lambda i: {
                    "transaction_hash": i["bank_hash"],
                    "account_id": i["groceries"],
                    "payee_id": MISSING,
                }
            ),
        ),
        ("/overrides/new", form(lambda i: {**i["split"], "split_account": ["x", i["dining"]]})),
        ("/overrides/new", form(lambda i: {**i["split"], "split_account": [MISSING, i["dining"]]})),
        ("/overrides/new", form(lambda i: {**i["split"], "split_amount": ["1", "nan"]})),
    ],
    "rules.overrides_delete": [("/overrides/nope/delete", form(lambda i: {}))],
    "rules.rules_delete": [(f"/rules/{MISSING}/delete", form(lambda i: {}))],
    "rules.rules_ignore": [(f"/rules/{MISSING}/ignore", form(lambda i: {}))],
    "rules.rules_new": [
        ("/rules/new", form(lambda i: {**i["rule"], "match_type": "nope"})),
        ("/rules/new", form(lambda i: {**i["rule"], "match_type": "regex", "pattern": "("})),
        ("/rules/new", form(lambda i: {**i["rule"], "pattern": ""})),
        ("/rules/new", form(lambda i: {**i["rule"], "account_id": MISSING})),
        ("/rules/new", form(lambda i: {**i["rule"], "priority": "hi"})),
        ("/rules/new", form(lambda i: {**i["rule"], "payee_id": MISSING})),
    ],
    "rules.rules_update": [
        ("/rules/{rule_id}/update", form(lambda i: {**i["rule"], "match_type": "nope"})),
        ("/rules/{rule_id}/update", form(lambda i: {**i["rule"], "account_id": "x"})),
    ],
    "rules.transfer_rules_delete": [(f"/transfer-rules/{MISSING}/delete", form(lambda i: {}))],
    "rules.transfer_rules_new": [
        ("/transfer-rules/new", form(lambda i: {"match_type": "nope", "pattern": "X"})),
        ("/transfer-rules/new", form(lambda i: {"match_type": "regex", "pattern": "("})),
        ("/transfer-rules/new", form(lambda i: {"match_type": "contains", "pattern": ""})),
    ],
    "rules.transfer_rules_update": [
        ("/transfer-rules/{transfer_rule_id}/update", form(lambda i: {"match_type": "nope", "pattern": "X"})),
    ],
    "settings.settings_save": [
        ("/settings", form(lambda i: {**i["settings"], "timezone": "Nowhere/Nope"})),
        ("/settings", form(lambda i: {**i["settings"], "base_currency": ""})),
        ("/settings", form(lambda i: {**i["settings"], "export_format": "pdf"})),
    ],
    "subscriptions.subscriptions_cancel": [(f"/subscriptions/{MISSING}/cancel", form(lambda i: {}))],
    "subscriptions.subscriptions_delete": [(f"/subscriptions/{MISSING}/delete", form(lambda i: {}))],
    "subscriptions.subscriptions_edit": [
        ("/subscriptions/{subscription_id}/edit", form(lambda i: {**i["subscription"], "cadence": "daily"})),
        (f"/subscriptions/{MISSING}/edit", form(lambda i: i["subscription"])),
    ],
    "subscriptions.subscriptions_new": [
        ("/subscriptions/new", form(lambda i: {**i["subscription"], "payee_id": "x"})),
        ("/subscriptions/new", form(lambda i: {**i["subscription"], "payee_id": MISSING})),
        ("/subscriptions/new", form(lambda i: {**i["subscription"], "amount": "0"})),
        ("/subscriptions/new", form(lambda i: {**i["subscription"], "amount": "1,000"})),
    ],
    "subscriptions.subscriptions_reactivate": [(f"/subscriptions/{MISSING}/reactivate", form(lambda i: {}))],
}

CASES = [(endpoint, path, payload) for endpoint, cases in MALFORMED.items() for path, payload in cases]


@pytest.fixture(autouse=True)
def isolated_files(tmp_path, monkeypatch):
    ingest = tmp_path / "ingest" / "pending"
    monkeypatch.setattr(uploads, "INGEST_DIR", ingest)
    monkeypatch.setattr(uploads, "MANIFEST_PATH", ingest.parent / "manifest.json")
    monkeypatch.setattr(service, "INGEST_DIR", ingest)
    monkeypatch.setattr(service, "EXPORT_DIR", tmp_path / "exports")


@pytest.fixture
def client(conn):
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    with app.test_client() as test_client:
        yield test_client


@pytest.fixture
def ids(conn, account_factory, transaction_factory, transfer_rule_factory):
    checking = account_factory("Test Checking")
    groceries = account_factory("Test Groceries", type="expense", budget=1)
    dining = account_factory("Test Dining", type="expense", budget=1)
    bank_hash = transaction_factory(checking, "2026-01-05", -450, "COFFEE SHOP")
    transfer_rule_factory("MOVE")

    rule_id = conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id) VALUES ('contains', 'COFFEE', ?)",
        (groceries,),
    ).lastrowid
    payee = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) VALUES ('Netflix', 'netflix', ?)",
        (dining,),
    ).lastrowid
    subscription_id = conn.execute(
        "INSERT INTO subscriptions (payee_id, amount_cents, cadence, first_seen_date) "
        "VALUES (?, -1500, 'monthly', '2026-01-01')",
        (payee,),
    ).lastrowid
    conn.execute("INSERT INTO projects (name) VALUES ('Test Project')")
    conn.execute(
        "INSERT INTO budgets (account_id, period, amount_cents) "
        "VALUES (?, date('now', 'start of month'), 5000)",
        (groceries,),
    )
    rebuild_ledger()

    return {
        "checking": checking,
        "groceries": groceries,
        "dining": dining,
        "unknown": conn.execute("SELECT id FROM accounts WHERE role = 'unknown'").fetchone()["id"],
        "bank_hash": bank_hash,
        "bank_id": conn.execute(
            "SELECT id FROM transactions WHERE transaction_hash = ?", (bank_hash,)
        ).fetchone()["id"],
        "rule_id": rule_id,
        "transfer_rule_id": conn.execute("SELECT id FROM transfer_rules").fetchone()["id"],
        "payee": payee,
        "subscription_id": subscription_id,
        "manual": {
            "from_account_id": checking,
            "category_account_id": groceries,
            "posted_date": "2026-01-05",
            "amount": "1",
        },
        "rule": {"match_type": "contains", "pattern": "X", "account_id": groceries},
        "split": {
            "mode": "split",
            "transaction_hash": bank_hash,
            "split_account": [groceries, dining],
            "split_amount": ["1", "3.50"],
        },
        "settings": {"timezone": "UTC", "base_currency": "USD", "export_format": "xlsx"},
        "subscription": {"payee_id": payee, "cadence": "monthly", "amount": "5"},
    }


def test_every_data_changing_route_is_covered(client):
    posts = {rule.endpoint for rule in client.application.url_map.iter_rules() if "POST" in rule.methods}

    assert posts == set(MALFORMED)


@pytest.mark.parametrize(
    ("endpoint", "path", "payload"),
    CASES,
    ids=[f"{endpoint}-{n}" for n, (endpoint, _, _) in enumerate(CASES)],
)
def test_malformed_input_is_rejected_without_changes(client, ids, endpoint, path, payload):
    kind, build = payload
    url = path.format(**ids)
    before = _state()

    if kind == "json":
        response = client.post(url, json=build(ids))
    else:
        response = client.post(url, data={key: _form_value(value, ids) for key, value in build(ids).items()})

    assert response.status_code < 500
    assert _state() == before


def _form_value(value, ids):
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return [str(item) for item in value]
    return str(value).format(**ids)


def _state():
    with db.transaction() as conn:
        tables = [row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        state = {
            table: sorted(repr(row) for row in conn.execute(f"SELECT * FROM {table}"))
            for table in tables
            if table != "ledger"
        }
        state["ledger"] = sorted(
            repr(row)
            for row in conn.execute("SELECT group_id, transaction_id, account_id, amount_cents FROM ledger")
        )

    files = uploads.INGEST_DIR.parent
    state["files"] = sorted(path.name for path in files.rglob("*")) if files.exists() else []
    return state


def test_exports_stream_without_touching_the_disk(client, ids):
    response = client.get(f"/accounts/export?account_id={ids['checking']}")

    assert response.status_code == 200
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert response.data[:2] == b"PK"
    assert not service.EXPORT_DIR.exists()


def test_saving_mappings_rebuilds_the_ledger(client, conn, ids):
    conn.execute("UPDATE transactions SET payee_id = ? WHERE id = ?", (ids["payee"], ids["bank_id"]))
    conn.execute("DELETE FROM account_rules")
    rebuild_ledger()

    change = {
        "op": "update",
        "table": "payees",
        "id": ids["payee"],
        "fields": {"account_id": ids["groceries"]},
    }
    response = client.post("/mappings/save", json={"changes": [change]})

    category = conn.execute(
        "SELECT account_id FROM ledger WHERE transaction_id = ? AND account_id != ?",
        (ids["bank_id"], ids["checking"]),
    ).fetchone()
    assert response.json["recategorized"] == 1
    assert category["account_id"] == ids["groceries"]


def test_forwarded_headers_are_trusted_only_when_configured(conn, monkeypatch):
    assert not isinstance(create_app().wsgi_app, ProxyFix)

    monkeypatch.setattr(config, "TRUSTED_PROXIES", 1)

    assert isinstance(create_app().wsgi_app, ProxyFix)


def test_tests_never_touch_the_real_data_folder():
    assert config.DATA_DIR != config.PROJECT_ROOT / "data"


@pytest.mark.parametrize(
    "path",
    [
        "/budget",
        "/expenses",
        "/expenses?mode=year&year=2026",
        "/accounts?account_id={checking}",
        "/accounts/view/{checking}",
        "/accounts/view/{groceries}",
        "/payees/view/{payee}",
        "/projects",
        "/charts",
        "/inbox",
        "/rules",
        "/mappings",
        "/subscriptions",
        "/import",
        "/settings",
        "/accounts/balance-sheet",
        "/expenses/export",
    ],
)
def test_every_page_and_export_answers(client, ids, path):
    response = client.get(path.format(**ids))

    assert response.status_code == 200, path


def test_net_worth_on_the_accounts_page_leaves_out_equity(client, conn, account_factory, transaction_factory):
    bank = account_factory("Test Bank")
    opening = account_factory("Test Opening", type="equity")
    hash_ = transaction_factory(bank, "2026-01-01", 300000, "OPENING")
    conn.execute(
        "INSERT INTO transactions_overrides (transaction_hash, account_id) VALUES (?, ?)", (hash_, opening)
    )
    conn.commit()
    rebuild_ledger()

    page = client.get(f"/accounts?account_id={bank}").get_data(as_text=True)
    net_worth = page[page.index("Net worth") :]

    assert "3,000.00" in net_worth[:200]
