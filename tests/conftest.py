import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest

from intent_ledger.db import db
from intent_ledger.importer.normalize import normalize_description


@pytest.fixture
def conn(tmp_path):
    original_path = db.db_path
    db.db_path = tmp_path / "test.db"
    db.initialize()

    with db.connect() as connection:
        yield connection

    db.db_path = original_path


@pytest.fixture
def account_factory(conn):
    def make(name, type="asset", **extra):
        fields = {"name": name, "type": type, **extra}
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        cursor = conn.execute(
            f"INSERT INTO accounts ({columns}) VALUES ({placeholders})",
            list(fields.values()),
        )
        conn.commit()
        return cursor.lastrowid

    return make


@pytest.fixture
def transfer_rule_factory(conn):
    def make(pattern, match_type="contains", priority=0):
        conn.execute(
            "INSERT INTO transfer_rules (match_type, pattern, priority) VALUES (?, ?, ?)",
            (match_type, pattern, priority),
        )
        conn.commit()

    return make


@pytest.fixture
def transaction_factory(conn):
    counter = iter(range(1, 100_000))

    def make(account_id, posted_date, amount_cents, description):
        transaction_hash = f"test-hash-{next(counter)}"
        normalized = normalize_description(description)
        conn.execute(
            """
            INSERT INTO transactions (
                account_id, posted_date, amount_cents,
                raw_description, normalized_description, transaction_hash
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (account_id, posted_date, amount_cents, description, normalized["payee"], transaction_hash),
        )
        conn.commit()
        return transaction_hash

    return make
