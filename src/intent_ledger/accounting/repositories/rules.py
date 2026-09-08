import sqlite3


class AccountRuleRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def list_ordered(self):
        return self._conn.execute("""
            SELECT match_type, pattern, account_id, payee_id
            FROM account_rules
            ORDER BY priority DESC, id
        """).fetchall()

    def list_with_account_name(self):
        return self._conn.execute("""
            SELECT
                ar.*,
                a.name AS account,
                p.canonical_name AS payee_name
            FROM account_rules ar
            JOIN accounts a
                ON a.id = ar.account_id
            LEFT JOIN payees p
                ON p.id = ar.payee_id
            ORDER BY ar.priority DESC, ar.pattern
        """).fetchall()

    def get(self, rule_id: int):
        return self._conn.execute(
            """
            SELECT ar.*, a.name AS account, p.canonical_name AS payee_name
            FROM account_rules ar
            JOIN accounts a
                ON a.id = ar.account_id
            LEFT JOIN payees p
                ON p.id = ar.payee_id
            WHERE ar.id = ?
        """,
            (rule_id,),
        ).fetchone()

    def create(
        self, match_type: str, pattern: str, account_id: int, priority: int, payee_id: int | None = None
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO account_rules (match_type, pattern, account_id, payee_id, priority)
            VALUES (?, ?, ?, ?, ?)
        """,
            (match_type, pattern, account_id, payee_id, priority),
        )
        return cursor.lastrowid

    def update(
        self,
        rule_id: int,
        match_type: str,
        pattern: str,
        account_id: int,
        priority: int,
        payee_id: int | None = None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE account_rules
            SET match_type = ?, pattern = ?, account_id = ?, payee_id = ?, priority = ?
            WHERE id = ?
        """,
            (match_type, pattern, account_id, payee_id, priority, rule_id),
        )

    def delete(self, rule_id: int) -> None:
        self._conn.execute("DELETE FROM account_rules WHERE id = ?", (rule_id,))

    def delete_learned(self, pattern: str) -> None:
        self._conn.execute(
            "DELETE FROM account_rules WHERE match_type = 'equals' AND pattern = ? AND priority = 100",
            (pattern,),
        )
