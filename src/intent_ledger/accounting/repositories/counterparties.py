import sqlite3

from intent_ledger.domain.models import Counterparty


class CounterpartyRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, counterparty_id: int) -> Counterparty | None:
        row = self._conn.execute(
            "SELECT id, name FROM counterparties WHERE id = ?", (counterparty_id,)
        ).fetchone()
        return Counterparty.from_row(row) if row else None

    def get_id_by_name(self, name: str) -> int | None:
        row = self._conn.execute("SELECT id FROM counterparties WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else None

    def list_all_ordered(self) -> list[Counterparty]:
        rows = self._conn.execute("SELECT id, name FROM counterparties ORDER BY name").fetchall()
        return [Counterparty.from_row(r) for r in rows]

    def assign_to_transactions(self, transaction_hashes: list[str], counterparty_id: int | None) -> None:
        self._conn.executemany(
            "UPDATE transactions SET counterparty_id = ? WHERE transaction_hash = ?",
            [(counterparty_id, th) for th in transaction_hashes],
        )
