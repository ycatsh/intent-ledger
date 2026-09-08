import sqlite3

from intent_ledger.accounting.payees import normalize
from intent_ledger.accounting.repositories.payees import PayeeRepository
from intent_ledger.accounting.repositories.rules_overrides import OverrideRepository
from intent_ledger.accounting.repositories.rules_splits import SplitRepository
from intent_ledger.accounting.rules import default_account_id, fetch_account_rules
from intent_ledger.db import db
from intent_ledger.importer.normalize import extract_payee_key

ALLOWED_FIELDS = {
    "payees": {"canonical_name", "normalized_name", "account_id", "is_system"},
    "accounts": {
        "name",
        "type",
        "parent_account_id",
        "institution",
        "account_number_last4",
        "budget",
        "is_active",
        "needs_review",
    },
    "payee_aliases": {"payee_id", "alias", "normalized_alias", "source", "usage_count", "last_seen"},
    "counterparties": {"name"},
}


def assert_allowed_fields_match_schema(conn) -> None:
    for table, fields in ALLOWED_FIELDS.items():
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        unknown = fields - columns

        if unknown:
            raise RuntimeError(
                f"ALLOWED_FIELDS[{table!r}] references column(s) not in the schema: {sorted(unknown)}"
            )


def unlink_payee(payee_name: str) -> dict:
    """Detach a payee from every transaction it is linked to and delete it.

    Before clearing payee_id, save the transaction's currently resolved account
    (its existing override, or the account it would use today based on the rule
    match or the payee's default) in transactions_overrides if it is not already
    there. This prevents deleting the payee from silently changing which account
    the transaction belongs to.
    """
    payee_name = (payee_name or "").strip()
    if not payee_name:
        raise ValueError("Payee name is required.")

    with db.transaction() as conn:
        payee_repo = PayeeRepository(conn)
        payee = payee_repo.find_by_canonical_name_ci(payee_name)

        if payee is None:
            raise ValueError(f"Payee {payee_name!r} not found.")

        payee_id = payee.id
        rules = fetch_account_rules(conn)

        transactions = conn.execute(
            "SELECT id, transaction_hash, raw_description FROM transactions WHERE payee_id = ?",
            (payee_id,),
        ).fetchall()

        overrides_created = 0
        override_repo = OverrideRepository(conn)
        split_repo = SplitRepository(conn)

        transaction_hashes = [t["transaction_hash"] for t in transactions]
        overridden_hashes = override_repo.hashes_with_overrides(transaction_hashes)
        split_hashes = split_repo.hashes_with_splits(transaction_hashes)

        for txn in transactions:
            has_override = txn["transaction_hash"] in overridden_hashes
            has_split = txn["transaction_hash"] in split_hashes

            if not has_override and not has_split:
                current_account_id = default_account_id(rules, conn, txn["raw_description"], payee_id)
                if override_repo.set_if_absent(txn["transaction_hash"], current_account_id):
                    overrides_created += 1

        conn.execute("UPDATE transactions SET payee_id = NULL WHERE payee_id = ?", (payee_id,))

        aliases_deleted = conn.execute("DELETE FROM payee_aliases WHERE payee_id = ?", (payee_id,)).rowcount

        payee_repo.delete(payee_id)

    return {
        "payee_name": payee.name,
        "transactions_unlinked": len(transactions),
        "overrides_created": overrides_created,
        "aliases_deleted": aliases_deleted,
    }


def _account_rows(conn, types, order_by):
    placeholders = ", ".join("?" for _ in types)
    return conn.execute(
        f"""
        SELECT
            a.id, a.name, a.type, a.parent_account_id, a.institution,
            a.account_number_last4, a.budget, a.is_active, a.needs_review,
            p.name AS parent_name, p.type AS parent_type
        FROM accounts a
        LEFT JOIN accounts p
            ON p.id = a.parent_account_id
        WHERE a.type IN ({placeholders})
        ORDER BY {order_by}
        """,
        types,
    ).fetchall()


def get_mappings_page():
    with db.transaction() as conn:
        balance_sheet_accounts = _account_rows(
            conn,
            ("asset", "liability", "equity"),
            "CASE a.type WHEN 'asset' THEN 0 WHEN 'liability' THEN 1 ELSE 2 END, a.name",
        )

        category_accounts = _account_rows(
            conn,
            ("income", "expense"),
            "a.needs_review DESC, CASE a.type WHEN 'income' THEN 0 ELSE 1 END, a.name",
        )

        payees = conn.execute("""
            SELECT
                m.id, m.canonical_name, m.normalized_name, m.account_id, m.is_system,
                COUNT(al.id) AS alias_count,
                acc.name AS account_name, acc.type AS account_type
            FROM payees m
            LEFT JOIN payee_aliases al
                ON al.payee_id = m.id
            LEFT JOIN accounts acc
                ON acc.id = m.account_id
            GROUP BY m.id
            ORDER BY m.is_system DESC, m.canonical_name
        """).fetchall()

        aliases = conn.execute("""
            SELECT
                al.id, al.payee_id, al.alias, al.normalized_alias,
                al.source, al.usage_count, al.last_seen,
                m.canonical_name AS payee_name
            FROM payee_aliases al
            JOIN payees m
                ON m.id = al.payee_id
            ORDER BY al.payee_id, al.alias
        """).fetchall()

        counterparties = conn.execute("""
            SELECT
                c.id, c.name,
                COUNT(t.id) AS usage_count
            FROM counterparties c
            LEFT JOIN transactions t
                ON t.counterparty_id = c.id
            GROUP BY c.id
            ORDER BY c.name
        """).fetchall()

    return balance_sheet_accounts, category_accounts, payees, aliases, counterparties


def save_mappings(changes):
    for change in changes:
        op, table = change.get("op"), change.get("table")

        if table not in ALLOWED_FIELDS:
            raise ValueError(f"unknown table {table!r}")
        if op not in ("insert", "update", "delete"):
            raise ValueError(f"unknown op {op!r}")
        if op == "insert" and not isinstance(change.get("temp_id"), str):
            raise ValueError("insert requires a temp_id")
        if op in ("update", "delete") and not isinstance(change.get("id"), int):
            raise ValueError(f"{op} requires an id")

        bad_fields = set(change.get("fields", {})) - ALLOWED_FIELDS[table]
        if bad_fields:
            raise ValueError(f"unknown fields {sorted(bad_fields)} for {table}")

    id_map = {}

    with db.transaction() as conn:
        for change in sorted(changes, key=lambda c: c["op"] != "insert"):
            _apply_change(conn, change, id_map)

    return id_map


def _validate_account_hierarchy(conn, account_id, fields):
    if "type" not in fields and "parent_account_id" not in fields:
        return

    account_type = fields.get("type")
    if account_type is None and account_id is not None:
        row = conn.execute("SELECT type FROM accounts WHERE id = ?", (account_id,)).fetchone()
        account_type = row["type"] if row else None

    if "parent_account_id" in fields:
        parent_id = fields["parent_account_id"]
    elif account_id is not None:
        row = conn.execute("SELECT parent_account_id FROM accounts WHERE id = ?", (account_id,)).fetchone()
        parent_id = row["parent_account_id"] if row else None
    else:
        parent_id = None

    if not parent_id:
        return

    parent = conn.execute("SELECT type FROM accounts WHERE id = ?", (parent_id,)).fetchone()
    if parent and account_type and parent["type"] != account_type:
        raise ValueError(f"A {account_type} account can't be nested under a {parent['type']} account.")


def _apply_change(conn, change, id_map):
    op, table = change["op"], change["table"]
    fields = {
        field: id_map.get(value, value) if isinstance(value, str) else value
        for field, value in change.get("fields", {}).items()
    }

    if table == "payees" and "canonical_name" in fields:
        fields["normalized_name"] = normalize(fields["canonical_name"])
    elif table == "payee_aliases" and "alias" in fields:
        fields["normalized_alias"] = extract_payee_key(fields["alias"])

    if table == "accounts" and op == "update" and fields:
        fields["needs_review"] = 0

    if table == "accounts" and op in ("insert", "update"):
        _validate_account_hierarchy(conn, change.get("id"), fields)

    try:
        if op == "insert":
            columns = list(fields)
            placeholders = ", ".join("?" for _ in columns)
            cursor = conn.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                [fields[c] for c in columns],
            )
            id_map[change["temp_id"]] = cursor.lastrowid

        elif op == "update":
            if not fields:
                return
            assignments = ", ".join(f"{c} = ?" for c in fields)
            conn.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?",
                [*fields.values(), change["id"]],
            )

        else:
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (change["id"],))

    except sqlite3.IntegrityError as e:
        raise ValueError(str(e)) from e
