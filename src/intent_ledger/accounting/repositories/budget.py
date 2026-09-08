import sqlite3


class BudgetRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get_amount_cents(self, account_id: int, period: str) -> int:
        row = self._conn.execute(
            """
            SELECT COALESCE(amount_cents, 0) AS amount_cents
            FROM budgets WHERE account_id = ? AND period = ?
        """,
            (account_id, period),
        ).fetchone()
        return row["amount_cents"] if row else 0

    def set_amount_cents(self, account_id: int, period: str, amount_cents: int) -> None:
        self._conn.execute(
            """
            INSERT INTO budgets (account_id, period, amount_cents)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id, period)
            DO UPDATE SET amount_cents = excluded.amount_cents
        """,
            (account_id, period, amount_cents),
        )

    def adjust_amount_cents(self, account_id: int, period: str, delta_cents: int) -> None:
        """Insert or add a delta as if every account started at 0 - a
        negative delta against a row that doesn't exist yet still lands at
        delta_cents, not 0.
        """
        self._conn.execute(
            """
            INSERT INTO budgets (account_id, period, amount_cents)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id, period)
            DO UPDATE SET amount_cents = amount_cents + ?
        """,
            (account_id, period, delta_cents, delta_cents),
        )

    def set_goal(self, account_id: int, target_cents: int, target_date: str | None) -> None:
        self._conn.execute(
            "UPDATE accounts SET goal_target_cents = ?, goal_target_date = ? WHERE id = ?",
            (target_cents, target_date, account_id),
        )

    def delete_goal(self, account_id: int) -> None:
        self._conn.execute(
            "UPDATE accounts SET goal_target_cents = NULL, goal_target_date = NULL WHERE id = ?",
            (account_id,),
        )
