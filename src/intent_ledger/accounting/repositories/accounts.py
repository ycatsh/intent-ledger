import sqlite3

from intent_ledger.domain.models import Account


class AccountRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, account_id: int) -> Account | None:
        row = self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return Account.from_row(row) if row else None

    def active_ordered_by_type(self) -> list[Account]:
        rows = self._conn.execute("""
            SELECT *
            FROM accounts
            WHERE is_active = 1
            ORDER BY type, name
        """).fetchall()
        return [Account.from_row(r) for r in rows]

    def all_names(self) -> list[str]:
        """Every account name, active or not - get_or_create_expense() matches
        on name alone, so an inactive account still counts as existing.
        """
        rows = self._conn.execute("SELECT name FROM accounts").fetchall()
        return [r["name"] for r in rows]

    def needs_review_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM accounts WHERE needs_review = 1").fetchone()
        return row["n"]

    def create(self, name: str, account_type: str) -> int:
        try:
            cursor = self._conn.execute(
                """
                INSERT INTO accounts (name, type, budget, is_active)
                VALUES (?, ?, 0, 1)
                """,
                (name, account_type),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f'An account named "{name}" already exists.') from e

        return cursor.lastrowid

    def get_or_create_expense(self, name: str) -> int:
        row = self._conn.execute("SELECT id FROM accounts WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        if row:
            return row["id"]

        cursor = self._conn.execute(
            """
            INSERT INTO accounts (name, type, budget, is_system, is_active, needs_review)
            VALUES (?, 'expense', 1, 0, 1, 1)
            """,
            (name,),
        )
        return cursor.lastrowid
