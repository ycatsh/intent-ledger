import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from flask import g

from intent_ledger.config import DATA_DIR

DB_PATH = DATA_DIR / "accounting.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_VERSION = 5
BUSY_TIMEOUT_SECONDS = 15

_open_transaction = ContextVar("open_transaction", default=None)


def dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@contextmanager
def savepoint(conn: sqlite3.Connection):
    conn.execute("SAVEPOINT isolated")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK TO isolated")
        conn.execute("RELEASE isolated")
        raise
    conn.execute("RELEASE isolated")


class Database:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
        conn.row_factory = dict_factory

        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA temp_store = MEMORY")

        return conn

    @contextmanager
    def transaction(self):
        """Yield a connection inside one write transaction.

        The transaction takes the write lock up front, so two requests can't
        both read the same state and then overwrite each other. A nested call
        on the same database joins the open transaction, so the outermost block
        commits or rolls back everything as one unit.
        """
        open_transaction = _open_transaction.get()
        if open_transaction is not None and open_transaction[0] == self.db_path:
            yield open_transaction[1]
            return

        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        token = _open_transaction.set((self.db_path, conn))

        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            _open_transaction.reset(token)
            conn.close()

    @contextmanager
    def bound(self, conn: sqlite3.Connection):
        token = _open_transaction.set((self.db_path, conn))
        try:
            yield conn
        finally:
            _open_transaction.reset(token)

    def session(self) -> sqlite3.Connection:
        if "db_conn" not in g:
            g.db_conn = self.connect()
        return g.db_conn

    def close_session(self, exc=None) -> None:
        conn = g.pop("db_conn", None)
        if conn is not None:
            conn.close()

    def initialize(self):
        conn = self.connect()

        try:
            if is_empty(conn):
                _create_schema(conn)

            version = conn.execute("PRAGMA user_version").fetchone()["user_version"]
            if version != SCHEMA_VERSION:
                raise RuntimeError(
                    f"{self.db_path} is at schema version {version}, and this release needs version "
                    f"{SCHEMA_VERSION}. Stop the app, run `intent-ledger migrate`, then start it again."
                )

            from intent_ledger.accounting.mappings import assert_allowed_fields_match_schema

            assert_allowed_fields_match_schema(conn)
        finally:
            conn.close()

    def vacuum(self):
        with self.connect() as conn:
            conn.execute("VACUUM")

    def analyze(self):
        with self.connect() as conn:
            conn.execute("ANALYZE")


def is_empty(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'").fetchone() is None


def _create_schema(conn: sqlite3.Connection) -> None:
    script = f"BEGIN IMMEDIATE;\n{SCHEMA_PATH.read_text()}\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"

    try:
        conn.executescript(script)
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


db = Database()
