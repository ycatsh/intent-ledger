import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from intent_ledger import migrate as migration
from intent_ledger.cli import main
from intent_ledger.db import SCHEMA_VERSION, Database, db

LEGACY_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v0.sql").read_text()

LEGACY_ROWS = """
INSERT INTO payees (canonical_name, normalized_name, account_id)
VALUES ('Netflix', 'netflix', (SELECT id FROM accounts WHERE name = 'Subscriptions'));

INSERT INTO transactions (
    id, account_id, posted_date, amount_cents, payee_id,
    raw_description, normalized_description, transaction_hash
)
VALUES
    (1, (SELECT id FROM accounts WHERE name = 'Checking'), '2026-01-05', -450, NULL,
     'UPI/ALICE K/alice@ok/rent/REF1', 'UPI RENT REF', 'h1'),
    (2, (SELECT id FROM accounts WHERE name = 'Checking'), '2026-01-31', -5000, NULL,
     'MOVE TO SAVINGS', 'MOVE TO SAVINGS', 'h2'),
    (3, (SELECT id FROM accounts WHERE name = 'Savings'), '2026-02-01', 5000, NULL,
     'MOVE FROM CHECKING', 'MOVE FROM CHECKING', 'h3'),
    (4, (SELECT id FROM accounts WHERE name = 'Checking'), '2026-01-10', -1500,
     (SELECT id FROM payees WHERE canonical_name = 'Netflix'), 'NETFLIX.COM', 'NETFLIX.COM', 'h4');

INSERT INTO account_rules (match_type, pattern, account_id, priority, needs_review)
VALUES ('equals', 'UPI RENT REF', (SELECT id FROM accounts WHERE name = 'Groceries'), 100, 1);

INSERT INTO transfer_rules (match_type, pattern) VALUES ('contains', 'MOVE');

INSERT INTO subscriptions (id, payee_id, account_id, amount_cents, cadence, first_seen_date)
VALUES (
    1,
    (SELECT id FROM payees WHERE canonical_name = 'Netflix'),
    (SELECT id FROM accounts WHERE name = 'Subscriptions'),
    -1500,
    'monthly',
    '2026-01-10'
);

INSERT INTO subscriptions_charges (subscription_id, transaction_id, amount_cents, posted_date)
VALUES (1, 4, -1500, '2026-01-10');

INSERT INTO ledger (group_id, transaction_id, account_id, amount_cents, description)
VALUES
    ('g1', 1, (SELECT id FROM accounts WHERE name = 'Checking'), -450, 'UPI/ALICE K/alice@ok/rent/REF1'),
    ('g1', 1, (SELECT id FROM accounts WHERE name = 'Groceries'), 450, 'UPI/ALICE K/alice@ok/rent/REF1'),
    ('g2', 2, (SELECT id FROM accounts WHERE name = 'Checking'), -5000, 'MOVE TO SAVINGS'),
    ('g2', 2, (SELECT id FROM accounts WHERE name = 'Savings'), 5000, 'MOVE TO SAVINGS'),
    ('g4', 4, (SELECT id FROM accounts WHERE name = 'Checking'), -1500, 'NETFLIX.COM'),
    ('g4', 4, (SELECT id FROM accounts WHERE name = 'Subscriptions'), 1500, 'NETFLIX.COM');
"""


def build(path: Path, schema: str = LEGACY_SCHEMA, rows: str = LEGACY_ROWS) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(schema + rows)
    conn.close()
    return path


def schema_of(path: Path) -> dict:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
    finally:
        conn.close()
    return {(kind, name): " ".join((sql or "").split()) for kind, name, sql in rows}


def query(path: Path, sql: str, params=()):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    path = build(tmp_path / "accounting.db")
    monkeypatch.setattr(db, "db_path", path)
    return path


def test_a_legacy_database_migrates_to_the_fresh_schema(legacy, tmp_path):
    fresh = Database(tmp_path / "fresh.db")
    fresh.initialize()

    report = migration.migrate()

    assert query(legacy, "PRAGMA user_version")[0][0] == SCHEMA_VERSION
    assert schema_of(legacy) == schema_of(fresh.db_path)
    assert f"Schema version 0 -> {SCHEMA_VERSION}." in report
    assert "1 learned rule(s) now match with contains at priority 50." in report
    assert "No transaction changes category." in report
    assert any(line.startswith("Backed up to") for line in report)
    assert len(list((tmp_path / "backups").glob("accounting-v0-*.db"))) == 1


def test_migrating_keeps_the_data_and_applies_every_fix(legacy):
    migration.migrate()

    rule = query(legacy, "SELECT match_type, priority, needs_review FROM account_rules")[0]
    savings_leg = query(
        legacy,
        """
        SELECT l.transaction_id, t.posted_date FROM ledger l
        JOIN transactions t ON t.id = l.transaction_id
        JOIN accounts a ON a.id = l.account_id
        WHERE a.name = 'Savings'
        """,
    )[0]

    assert query(legacy, "SELECT COUNT(*) FROM transactions")[0][0] == 4
    assert tuple(rule) == ("contains", 50, 1)
    assert tuple(savings_leg) == (3, "2026-02-01")
    assert {row[0] for row in query(legacy, "SELECT typeof(group_id) FROM ledger")} == {"integer"}
    assert [tuple(row) for row in query(legacy, "SELECT * FROM subscriptions_charges")] == [
        (1, 4, -1500, "2026-01-10")
    ]


def test_a_dry_run_reports_without_writing(legacy):
    before = legacy.read_bytes()

    report = migration.migrate(dry_run=True)

    assert report[0].startswith("Dry run on a copy of")
    assert f"Schema version 0 -> {SCHEMA_VERSION}." in report
    assert legacy.read_bytes() == before


def test_a_failing_step_leaves_the_database_on_its_old_version(legacy, monkeypatch):
    def broken_step(conn):
        raise RuntimeError("step failed")

    monkeypatch.setattr(migration, "STEPS", (*migration.STEPS[:2], broken_step, *migration.STEPS[3:]))

    with pytest.raises(RuntimeError):
        migration.migrate()

    assert query(legacy, "PRAGMA user_version")[0][0] == 0
    assert query(legacy, "SELECT match_type FROM account_rules")[0][0] == "equals"
    assert ("table", "schema_version") in schema_of(legacy)


def test_a_database_from_before_the_last_columns_catches_up(tmp_path, monkeypatch):
    oldest = LEGACY_SCHEMA.replace("    default_parser_slug TEXT,\n", "").replace(
        "    priority INTEGER DEFAULT 0,\n    needs_review BOOLEAN NOT NULL DEFAULT 0,\n",
        "    priority INTEGER DEFAULT 0,\n",
    )
    rows = LEGACY_ROWS.replace("priority, needs_review)", "priority)").replace("100, 1);", "100);")
    path = build(tmp_path / "accounting.db", oldest, rows)
    monkeypatch.setattr(db, "db_path", path)

    migration.migrate()

    assert "default_parser_slug" in {row[1] for row in query(path, "PRAGMA table_info(accounts)")}
    assert "needs_review" in {row[1] for row in query(path, "PRAGMA table_info(account_rules)")}
    db.initialize()


def test_the_app_refuses_to_start_on_an_old_schema(legacy):
    with pytest.raises(RuntimeError, match="intent-ledger migrate"):
        db.initialize()


def test_migrate_reports_when_there_is_nothing_to_do(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "db_path", tmp_path / "missing.db")
    assert migration.migrate() == ["No database yet. The app creates a fresh one on first start."]

    db.initialize()
    assert migration.migrate() == [f"Already at schema version {SCHEMA_VERSION}. Nothing to do."]


def test_the_migrate_command_runs_without_building_the_app(legacy):
    result = CliRunner().invoke(main, ["migrate", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Dry run on a copy of" in result.output
