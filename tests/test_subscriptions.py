import pytest

from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.manual import add_manual_transaction, delete_manual_transaction
from intent_ledger.service import ledger_change


def charges(conn):
    return conn.execute(
        "SELECT subscription_id, transaction_id, amount_cents "
        "FROM subscriptions_charges ORDER BY transaction_id"
    ).fetchall()


def subscribe(conn, payee_id, account_id, cancelled_at=None):
    return conn.execute(
        """
        INSERT INTO subscriptions (
            payee_id, account_id, amount_cents, cadence, first_seen_date, status, cancelled_at
        )
        VALUES (?, ?, -1500, 'monthly', '2026-01-01', ?, ?)
        """,
        (payee_id, account_id, "cancelled" if cancelled_at else "active", cancelled_at),
    ).lastrowid


@pytest.fixture
def netflix(conn, account_factory):
    checking = account_factory("Test Checking")
    streaming = account_factory("Test Streaming", type="expense")

    for day in ("2026-01-05", "2026-02-05"):
        add_manual_transaction(
            {
                "from_account_id": checking,
                "category_account_id": streaming,
                "payee_name": "Netflix",
                "posted_date": day,
                "amount_cents": 1500,
            }
        )
    rebuild_ledger()

    payee_id = conn.execute("SELECT id FROM payees WHERE canonical_name = 'Netflix'").fetchone()["id"]
    return payee_id, streaming


def test_the_oldest_matching_subscription_claims_each_charge(conn, netflix):
    first = subscribe(conn, *netflix)
    subscribe(conn, *netflix)

    rows = charges(conn)

    assert len(rows) == 2
    assert {row["subscription_id"] for row in rows} == {first}
    assert {row["amount_cents"] for row in rows} == {-1500}


def test_a_cancelled_subscription_stops_claiming_charges_after_it_ends(conn, netflix):
    subscribe(conn, *netflix, cancelled_at="2026-01-31")

    assert len(charges(conn)) == 1


def test_deleting_a_charged_manual_transaction_works(conn, netflix):
    subscribe(conn, *netflix)
    first_hash = conn.execute(
        "SELECT transaction_hash FROM transactions ORDER BY posted_date LIMIT 1"
    ).fetchone()["transaction_hash"]

    with ledger_change():
        delete_manual_transaction(first_hash)

    assert len(charges(conn)) == 1
