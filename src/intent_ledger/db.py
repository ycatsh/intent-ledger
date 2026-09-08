import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import g

from intent_ledger.config import DATA_DIR

DB_PATH = DATA_DIR / "accounting.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class Database:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = dict_factory

        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA temp_store = MEMORY")

        return conn

    @contextmanager
    def transaction(self):
        conn = self.connect()

        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def session(self) -> sqlite3.Connection:
        if "db_conn" not in g:
            g.db_conn = self.connect()
        return g.db_conn

    def close_session(self, exc=None) -> None:
        conn = g.pop("db_conn", None)
        if conn is not None:
            conn.close()

    def initialize(self):
        if not SCHEMA_PATH.exists():
            raise FileNotFoundError(f"schema not found: {SCHEMA_PATH}")

        schema = SCHEMA_PATH.read_text()

        with self.transaction() as conn:
            conn.executescript(schema)

            from intent_ledger.accounting.mappings import assert_allowed_fields_match_schema

            assert_allowed_fields_match_schema(conn)

    def vacuum(self):
        with self.connect() as conn:
            conn.execute("VACUUM")

    def analyze(self):
        with self.connect() as conn:
            conn.execute("ANALYZE")


db = Database()
