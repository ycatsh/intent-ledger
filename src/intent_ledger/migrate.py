import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from intent_ledger.accounting.ledger import rebuild_ledger
from intent_ledger.accounting.rules import LEARNED_PRIORITY
from intent_ledger.db import SCHEMA_VERSION, db, dict_factory, is_empty
from intent_ledger.importer.normalize import extract_payee_key

SHOWN_CHANGES = 20
KEPT_BACKUPS = 10

COLUMNS_ADDED_BEFORE_MIGRATIONS = {
    "accounts": ("default_parser_slug", "TEXT"),
    "account_rules": ("needs_review", "BOOLEAN NOT NULL DEFAULT 0"),
}


def _v1_catch_up_older_databases(conn):
    for table, (column, declaration) in COLUMNS_ADDED_BEFORE_MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    conn.execute("DROP TABLE IF EXISTS schema_version")


def _v2_learned_rules_become_contains(conn):
    conn.execute(
        """
        UPDATE account_rules
        SET match_type = 'contains', priority = ?
        WHERE match_type = 'equals' AND priority = 100
        """,
        (LEARNED_PRIORITY,),
    )


def _v3_subscription_charges_become_a_view(conn):
    conn.execute("DROP TABLE subscriptions_charges")
    conn.execute("""
        CREATE VIEW subscriptions_charges AS
        SELECT DISTINCT
            s.id AS subscription_id,
            t.id AS transaction_id,
            -l.amount_cents AS amount_cents,
            t.posted_date
        FROM subscriptions s
        JOIN transactions t
            ON t.payee_id = s.payee_id
           AND (s.cancelled_at IS NULL OR t.posted_date <= s.cancelled_at)
        JOIN ledger l
            ON l.transaction_id = t.id
           AND l.amount_cents = -s.amount_cents
        JOIN accounts a
            ON a.id = l.account_id
           AND a.type = 'expense'
        WHERE NOT EXISTS (
            SELECT 1
            FROM subscriptions earlier
            WHERE earlier.payee_id = s.payee_id
              AND earlier.amount_cents = s.amount_cents
              AND earlier.id < s.id
              AND (earlier.cancelled_at IS NULL OR t.posted_date <= earlier.cancelled_at)
        )
    """)


def _v4_ledger_groups_become_integers(conn):
    conn.execute("DROP TABLE ledger")
    conn.execute("""
        CREATE TABLE ledger (
            id INTEGER PRIMARY KEY,
            group_id INTEGER NOT NULL,
            transaction_id INTEGER,
            account_id INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL,
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (transaction_id) REFERENCES transactions(id),
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        )
    """)
    conn.execute("CREATE INDEX idx_ledger_account ON ledger(account_id)")
    conn.execute("CREATE INDEX idx_ledger_transaction ON ledger(transaction_id)")
    conn.execute("CREATE INDEX idx_ledger_group ON ledger(group_id)")


STEPS = (
    _v1_catch_up_older_databases,
    _v2_learned_rules_become_contains,
    _v3_subscription_charges_become_a_view,
    _v4_ledger_groups_become_integers,
)


def migrate(dry_run: bool = False) -> list[str]:
    """Upgrade the database to this release's schema.

    Every step, the ledger rebuild, and a foreign key check run in one
    transaction, so a failure anywhere leaves the database on its old version.

    Returns:
        Report lines: what changed, row counts, and moved categories.
    """
    path = db.db_path
    version = _version(path) if path.exists() else None

    if version is None:
        return ["No database yet. The app creates a fresh one on first start."]
    if version == SCHEMA_VERSION:
        return [f"Already at schema version {SCHEMA_VERSION}. Nothing to do."]

    if dry_run:
        return _dry_run(path)

    report = [f"Backed up to {_backup(path, version)}."]

    conn = db.connect()
    try:
        report += _upgrade(conn)
    finally:
        conn.close()

    return report


def _version(path: Path) -> int | None:
    conn = sqlite3.connect(path)
    try:
        if is_empty(conn):
            return None
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _dry_run(path: Path) -> list[str]:
    source = sqlite3.connect(path)
    conn = sqlite3.connect(":memory:", isolation_level=None)

    try:
        source.execute("PRAGMA query_only = ON")
        source.backup(conn)
        conn.row_factory = dict_factory
        conn.execute("PRAGMA foreign_keys = ON")
        return [f"Dry run on a copy of {path}. Nothing was written.", *_upgrade(conn)]
    finally:
        source.close()
        conn.close()


def _backup(path: Path, version: int) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = path.parent / "backups" / f"{path.stem}-v{version}-{stamp}.db"
    target.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(path)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"The backup at {target} failed its integrity check. Nothing was migrated.")
    finally:
        source.close()
        copy.close()

    backups = sorted(target.parent.glob(f"{path.stem}-v*.db"), key=lambda backup: backup.stat().st_mtime)
    for stale in backups[:-KEPT_BACKUPS]:
        stale.unlink()

    return target


def _upgrade(conn) -> list[str]:
    version = conn.execute("PRAGMA user_version").fetchone()["user_version"]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"The database is at version {version}, newer than this release ({SCHEMA_VERSION})."
        )

    rows_before = _row_counts(conn)
    categories_before = _categories(conn)
    learned = _learned_rule_count(conn) if version < 2 else 0

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for step in STEPS[version:]:
            step(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        with db.bound(conn):
            rebuild_ledger()

        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            details = "\n".join(_describe_broken_reference(conn, row) for row in problems[:SHOWN_CHANGES])
            raise RuntimeError(
                f"These rows point at rows that don't exist. Fix them, then run this again:\n{details}"
            )

        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    return [
        f"Schema version {version} -> {SCHEMA_VERSION}.",
        *(
            [f"{learned} learned rule(s) now match with contains at priority {LEARNED_PRIORITY}."]
            if learned
            else []
        ),
        *_row_count_lines(rows_before, _row_counts(conn)),
        *_category_lines(conn, categories_before, _categories(conn)),
        *_broad_learned_rule_lines(conn),
    ]


def _learned_rule_count(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM account_rules WHERE match_type = 'equals' AND priority = 100"
    ).fetchone()
    return row["n"]


def _describe_broken_reference(conn, problem) -> str:
    row = conn.execute(f"SELECT * FROM {problem['table']} WHERE rowid = ?", (problem["rowid"],)).fetchone()
    return f"  {problem['table']} {dict(row) if row else problem['rowid']} -> missing {problem['parent']} row"


def _row_counts(conn) -> dict[str, int]:
    names = [
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        if not row["name"].startswith("sqlite_")
    ]
    return {name: conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"] for name in names}


def _row_count_lines(before: dict[str, int], after: dict[str, int]) -> list[str]:
    changed = [
        f"  {name}: {before.get(name, 0)} -> {after.get(name, 0)} rows"
        for name in sorted(before.keys() | after.keys())
        if before.get(name, 0) != after.get(name, 0)
    ]
    return ["Row counts changed:", *changed] if changed else ["No table changed size."]


def _categories(conn) -> dict[int, tuple[int, ...]]:
    rows = conn.execute("""
        SELECT own.transaction_id, other.account_id
        FROM ledger own
        JOIN transactions t ON t.id = own.transaction_id AND t.account_id = own.account_id
        JOIN ledger other ON other.group_id = own.group_id AND other.account_id != own.account_id
    """)

    grouped = {}
    for row in rows:
        grouped.setdefault(row["transaction_id"], Counter())[row["account_id"]] += 1

    return {txn_id: tuple(sorted(counts.elements())) for txn_id, counts in grouped.items()}


def _category_lines(conn, before, after) -> list[str]:
    changed = sorted(txn_id for txn_id in before.keys() & after.keys() if before[txn_id] != after[txn_id])
    if not changed:
        return ["No transaction changes category."]

    names = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM accounts")}
    shown = changed[:SHOWN_CHANGES]
    details = conn.execute(
        f"SELECT id, posted_date, raw_description, amount_cents FROM transactions "
        f"WHERE id IN ({','.join('?' * len(shown))})",
        shown,
    )

    def label(account_ids):
        return ", ".join(names.get(account_id, "?") for account_id in account_ids)

    lines = [f"{len(changed)} transaction(s) change category:"]
    lines += [
        f"  {row['posted_date']}  {row['amount_cents'] / 100:>12.2f}  {row['raw_description'][:40]:<40}  "
        f"{label(before[row['id']])} -> {label(after[row['id']])}"
        for row in details
    ]
    if len(changed) > SHOWN_CHANGES:
        lines.append(f"  and {len(changed) - SHOWN_CHANGES} more.")

    return lines


def _broad_learned_rule_lines(conn) -> list[str]:
    rules = conn.execute(
        "SELECT pattern FROM account_rules WHERE match_type = 'contains' AND priority = ?",
        (LEARNED_PRIORITY,),
    ).fetchall()
    descriptions = [
        row["raw_description"] for row in conn.execute("SELECT raw_description FROM transactions")
    ]

    broad = []
    for rule in rules:
        pattern = rule["pattern"].upper()
        strays = sum(1 for d in descriptions if pattern in d.upper() and extract_payee_key(d) != pattern)
        if strays:
            broad.append(f"  contains '{rule['pattern']}' also matches {strays} other description(s)")

    return ["Learned rules that match more than their own payee:", *broad] if broad else []
