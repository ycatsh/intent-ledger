import sqlite3


class ProjectRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, project_id: int):
        return self._conn.execute(
            """
            SELECT id, name, type, start_date, end_date, budget_cents, notes, archived
            FROM projects
            WHERE id = ?
        """,
            (project_id,),
        ).fetchone()

    def get_id_by_name(self, name: str) -> int | None:
        row = self._conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else None

    def list_active(self):
        return self._conn.execute("""
            SELECT id, name
            FROM projects
            WHERE archived = 0
            ORDER BY name
        """).fetchall()

    def create(
        self,
        name: str,
        project_type: str | None,
        start_date: str | None,
        end_date: str | None,
        budget_cents: int | None,
        notes: str | None,
    ) -> int:
        try:
            cursor = self._conn.execute(
                """
                INSERT INTO projects (name, type, start_date, end_date, budget_cents, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (name, project_type, start_date, end_date, budget_cents, notes),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f'A project named "{name}" already exists.') from e

        return cursor.lastrowid

    def delete(self, project_id: int) -> None:
        self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def assign_transactions(self, transaction_hashes: list[str], project_id: int) -> None:
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO transactions_projects (transaction_hash, project_id)
            VALUES (?, ?)
        """,
            [(th, project_id) for th in transaction_hashes],
        )

    def unassign_transactions(self, transaction_hashes: list[str], project_id: int) -> None:
        self._conn.executemany(
            """
            DELETE FROM transactions_projects
            WHERE transaction_hash = ? AND project_id = ?
        """,
            [(th, project_id) for th in transaction_hashes],
        )
