import sqlite3

from intent_ledger.settings import today_in


class SubscriptionRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, subscription_id: int):
        return self._conn.execute("SELECT id FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()

    def create(self, fields: dict) -> int:
        try:
            cursor = self._conn.execute(
                """
                INSERT INTO subscriptions (
                    payee_id, account_id, name, amount_cents,
                    cadence, first_seen_date, notes
                )
                VALUES (
                    :payee_id, :account_id, :name, :amount_cents,
                    :cadence, :first_seen_date, :notes
                )
            """,
                fields,
            )
        except sqlite3.IntegrityError as e:
            raise ValueError("Payee or category not found.") from e

        return cursor.lastrowid

    def update(self, fields: dict) -> None:
        try:
            self._conn.execute(
                """
                UPDATE subscriptions
                SET payee_id = :payee_id, account_id = :account_id, name = :name,
                    amount_cents = :amount_cents, cadence = :cadence, notes = :notes
                WHERE id = :id
            """,
                fields,
            )
        except sqlite3.IntegrityError as e:
            raise ValueError("Payee or category not found.") from e

    def cancel(self, subscription_id: int) -> None:
        self._conn.execute(
            """
            UPDATE subscriptions
            SET status = 'cancelled', cancelled_at = ?
            WHERE id = ?
        """,
            (today_in(self._conn).isoformat(), subscription_id),
        )

    def reactivate(self, subscription_id: int) -> None:
        self._conn.execute(
            """
            UPDATE subscriptions
            SET status = 'active', cancelled_at = NULL
            WHERE id = ?
        """,
            (subscription_id,),
        )

    def charge_count(self, subscription_id: int) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM subscriptions_charges WHERE subscription_id = ?",
            (subscription_id,),
        ).fetchone()["n"]

    def delete(self, subscription_id: int) -> None:
        self._conn.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
