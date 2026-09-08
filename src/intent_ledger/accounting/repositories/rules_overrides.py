import sqlite3


class OverrideRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get_account_id(self, transaction_hash: str) -> int | None:
        row = self._conn.execute(
            """
            SELECT account_id
            FROM transactions_overrides
            WHERE transaction_hash = ?
        """,
            (transaction_hash,),
        ).fetchone()
        return row["account_id"] if row else None

    def set(self, transaction_hash: str, account_id: int) -> None:
        self._conn.execute(
            """
            INSERT INTO transactions_overrides (transaction_hash, account_id)
            VALUES (?, ?)
            ON CONFLICT(transaction_hash)
            DO UPDATE SET account_id = excluded.account_id
        """,
            (transaction_hash, account_id),
        )

    def set_if_absent(self, transaction_hash: str, account_id: int) -> bool:
        """Return whether a row was actually inserted, so a caller can
        count real changes without a separate existence check first.
        """
        cursor = self._conn.execute(
            """
            INSERT INTO transactions_overrides (transaction_hash, account_id)
            VALUES (?, ?)
            ON CONFLICT(transaction_hash) DO NOTHING
        """,
            (transaction_hash, account_id),
        )
        return cursor.rowcount > 0

    def delete(self, transaction_hash: str) -> None:
        self._conn.execute(
            "DELETE FROM transactions_overrides WHERE transaction_hash = ?",
            (transaction_hash,),
        )

    def hashes_with_overrides(self, transaction_hashes) -> set:
        transaction_hashes = list(transaction_hashes)
        if not transaction_hashes:
            return set()

        placeholders = ",".join("?" * len(transaction_hashes))
        rows = self._conn.execute(
            f"SELECT DISTINCT transaction_hash FROM transactions_overrides "
            f"WHERE transaction_hash IN ({placeholders})",
            transaction_hashes,
        ).fetchall()
        return {row["transaction_hash"] for row in rows}
