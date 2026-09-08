import sqlite3
from collections.abc import Iterable

SplitLine = tuple[int, int, str | None]


class SplitRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def list_for_transaction(self, transaction_hash: str):
        return self._conn.execute(
            """
            SELECT account_id, amount_cents, note
            FROM transactions_splits
            WHERE transaction_hash = ?
            ORDER BY id
        """,
            (transaction_hash,),
        ).fetchall()

    def replace(self, transaction_hash: str, lines: Iterable[SplitLine]) -> None:
        self.delete(transaction_hash)
        for account_id, amount_cents, note in lines:
            self._conn.execute(
                """
                INSERT INTO transactions_splits (transaction_hash, account_id, amount_cents, note)
                VALUES (?, ?, ?, ?)
            """,
                (transaction_hash, account_id, amount_cents, note),
            )

    def delete(self, transaction_hash: str) -> None:
        self._conn.execute(
            "DELETE FROM transactions_splits WHERE transaction_hash = ?",
            (transaction_hash,),
        )

    def hashes_with_splits(self, transaction_hashes) -> set:
        transaction_hashes = list(transaction_hashes)
        if not transaction_hashes:
            return set()

        placeholders = ",".join("?" * len(transaction_hashes))
        rows = self._conn.execute(
            f"SELECT DISTINCT transaction_hash FROM transactions_splits "
            f"WHERE transaction_hash IN ({placeholders})",
            transaction_hashes,
        ).fetchall()
        return {row["transaction_hash"] for row in rows}
