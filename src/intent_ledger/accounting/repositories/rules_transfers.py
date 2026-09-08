import sqlite3


class TransferRuleRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def list_ordered_for_matching(self):
        return self._conn.execute("""
            SELECT match_type, pattern
            FROM transfer_rules
            ORDER BY priority DESC, id
        """).fetchall()

    def list_for_display(self):
        return self._conn.execute("""
            SELECT id, match_type, pattern, priority
            FROM transfer_rules
            ORDER BY priority DESC, pattern
        """).fetchall()

    def get(self, rule_id: int):
        return self._conn.execute(
            """
            SELECT id, match_type, pattern, priority
            FROM transfer_rules
            WHERE id = ?
        """,
            (rule_id,),
        ).fetchone()

    def create(self, match_type: str, pattern: str) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO transfer_rules (match_type, pattern)
            VALUES (?, ?)
        """,
            (match_type, pattern),
        )
        return cursor.lastrowid

    def update(self, rule_id: int, match_type: str, pattern: str) -> None:
        self._conn.execute(
            """
            UPDATE transfer_rules
            SET match_type = ?, pattern = ?
            WHERE id = ?
        """,
            (match_type, pattern, rule_id),
        )

    def delete(self, rule_id: int) -> None:
        self._conn.execute("DELETE FROM transfer_rules WHERE id = ?", (rule_id,))
