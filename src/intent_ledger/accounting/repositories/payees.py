import sqlite3

from intent_ledger.domain.models import Payee


class PayeeRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, payee_id: int) -> Payee | None:
        row = self._conn.execute("SELECT * FROM payees WHERE id = ?", (payee_id,)).fetchone()
        return Payee.from_row(row) if row else None

    def find_by_normalized_name(self, normalized_name: str) -> Payee | None:
        row = self._conn.execute(
            "SELECT * FROM payees WHERE normalized_name = ?", (normalized_name,)
        ).fetchone()
        return Payee.from_row(row) if row else None

    def find_by_canonical_name_ci(self, name: str) -> Payee | None:
        row = self._conn.execute(
            "SELECT * FROM payees WHERE canonical_name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        return Payee.from_row(row) if row else None

    def list_all_ordered(self) -> list[Payee]:
        rows = self._conn.execute("""
            SELECT id, canonical_name, account_id, is_system
            FROM payees
            ORDER BY is_system DESC, canonical_name
        """).fetchall()
        return [Payee.from_row(r) for r in rows]

    def default_account_names(self) -> dict[str, str]:
        """Payee name -> the name of the category it defaults to."""
        rows = self._conn.execute("""
            SELECT p.canonical_name AS payee, a.name AS account
            FROM payees p
            JOIN accounts a ON a.id = p.account_id
        """).fetchall()
        return {r["payee"]: r["account"] for r in rows}

    def create(self, name: str, normalized_name: str, account_id: int | None) -> Payee:
        cursor = self._conn.execute(
            """
            INSERT INTO payees (canonical_name, normalized_name, account_id)
            VALUES (?, ?, ?)
            """,
            (name, normalized_name, account_id),
        )
        return Payee(id=cursor.lastrowid, name=name, account_id=account_id)

    def delete(self, payee_id: int) -> None:
        self._conn.execute("DELETE FROM payees WHERE id = ?", (payee_id,))

    def set_account_id(self, payee_id: int, account_id: int) -> None:
        self._conn.execute("UPDATE payees SET account_id = ? WHERE id = ?", (account_id, payee_id))
