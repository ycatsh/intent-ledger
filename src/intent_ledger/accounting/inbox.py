from collections import Counter
from difflib import SequenceMatcher

from intent_ledger.accounting.accounts import get_or_create_expense_account
from intent_ledger.accounting.payees import get_or_create_payee, normalize
from intent_ledger.accounting.repositories.payees import PayeeRepository
from intent_ledger.accounting.repositories.rules_overrides import OverrideRepository
from intent_ledger.accounting.rules import get_payee_account_id, learn_account_rule
from intent_ledger.db import db
from intent_ledger.importer.normalize import extract_payee_key

_SIMILARITY_THRESHOLD = 0.75
_COMMON_TOKEN_MIN_GROUPS = 3
_COMMON_TOKEN_RATIO = 0.3


def get_unknown_txn_count():
    with db.transaction() as conn:
        total = conn.execute(
            """
            SELECT COUNT(*)
            FROM ledger l
            JOIN accounts a
                ON a.id = l.account_id
            WHERE a.name = 'Unknown'
            """
        ).fetchone()["COUNT(*)"]
    return total


def get_unknown_txn_groups(smart=False, flat=False):
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT
                t.id,
                t.raw_description,
                t.normalized_description,
                t.posted_date,
                -l.amount_cents / 100.0 AS amount,
                ta.name AS account
            FROM ledger l
            JOIN accounts a
                ON a.id = l.account_id
            JOIN transactions t
                ON t.id = l.transaction_id
            LEFT JOIN accounts ta
                ON ta.id = t.account_id
            WHERE a.name = 'Unknown'
            ORDER BY t.posted_date DESC
            """
        ).fetchall()

    groups = {}

    for row in rows:
        if flat:
            key = str(row["id"])
        else:
            key = extract_payee_key(row["normalized_description"] or row["raw_description"])

        group = groups.setdefault(
            key,
            {
                "id": row["id"],
                "raw": row["raw_description"],
                "count": 0,
                "latest_date": row["posted_date"],
                "total_amount": 0.0,
                "accounts": set(),
                "member_ids": [],
                "members": [],
            },
        )

        group["count"] += 1
        group["total_amount"] += row["amount"]
        group["member_ids"].append(row["id"])
        group["accounts"].add(row["account"])
        group["members"].append(
            {
                "id": row["id"],
                "date": row["posted_date"],
                "raw": row["raw_description"],
                "amount": row["amount"],
            }
        )
        if row["posted_date"] > group["latest_date"]:
            group["latest_date"] = row["posted_date"]

    if smart and not flat:
        groups = _merge_similar_groups(groups)

    ordered = sorted(groups.values(), key=lambda g: (g["latest_date"], g["count"]), reverse=True)

    for group in ordered:
        group["accounts"] = ", ".join(sorted(group["accounts"]))
        group["members"].sort(key=lambda m: m["date"], reverse=True)

    return ordered


def _merge_similar_groups(groups: dict) -> dict:
    keys = [k for k in groups if k]

    if len(keys) < 2:
        return groups

    significant = _primary_tokens(keys)

    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            sig_a, sig_b = significant[a], significant[b]
            if not sig_a or not sig_b:
                continue
            if _similarity(sig_a, sig_b) >= _SIMILARITY_THRESHOLD:
                union(a, b)

    merged = {}

    for key in keys:
        root = find(key)
        source = groups[key]
        target = merged.setdefault(
            root,
            {
                "id": source["id"],
                "raw": source["raw"],
                "count": 0,
                "latest_date": source["latest_date"],
                "total_amount": 0.0,
                "accounts": set(),
                "member_ids": [],
                "members": [],
            },
        )

        target["count"] += source["count"]
        target["total_amount"] += source["total_amount"]
        target["member_ids"].extend(source["member_ids"])
        target["accounts"] |= source["accounts"]
        target["members"].extend(source["members"])

        if source["latest_date"] >= target["latest_date"]:
            target["latest_date"] = source["latest_date"]
            target["raw"] = source["raw"]
            target["id"] = source["id"]

    if "" in groups:
        merged[""] = groups[""]

    return merged


def _primary_tokens(keys: list[str]) -> dict[str, str]:
    primaries = {key: (key.split()[0] if key.split() else "") for key in keys}

    doc_freq = Counter(primaries.values())
    total = len(keys)
    common = {
        token
        for token, count in doc_freq.items()
        if count >= _COMMON_TOKEN_MIN_GROUPS and count / total >= _COMMON_TOKEN_RATIO
    }

    return {key: ("" if token in common else token) for key, token in primaries.items()}


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def assign_all(assignments):
    with db.transaction() as conn:
        for txn_id, payee_name, account_name in assignments:
            _assign(conn, txn_id, payee_name, account_name)


def _assign(conn, transaction_id: int, payee_name: str, account_name: str):
    payee_name = (payee_name or "").strip()
    account_name = (account_name or "").strip()

    if not account_name:
        raise ValueError("Category is required.")

    account_id = get_or_create_expense_account(conn, account_name)

    if not payee_name:
        txn = conn.execute(
            "SELECT raw_description, normalized_description FROM transactions WHERE id = ?",
            (transaction_id,),
        ).fetchone()
        if txn is None:
            raise ValueError("Transaction not found.")

        learn_account_rule(conn, txn["normalized_description"] or txn["raw_description"], account_id)
        _set_override(conn, transaction_id, account_id)
        return

    payee_id = get_or_create_payee(conn, payee_name, normalize(payee_name), account_id)
    payee_id = _assign_payee(conn, transaction_id, payee_id)
    default_account_id = get_payee_account_id(conn, payee_id)

    if default_account_id is None:
        PayeeRepository(conn).set_account_id(payee_id, account_id)
    elif account_id != default_account_id:
        _set_override(conn, transaction_id, account_id)


def _assign_payee(conn, transaction_id: int, payee_id: int):
    txn = conn.execute(
        """
        SELECT
            raw_description,
            normalized_description,
            posted_date
        FROM transactions
        WHERE id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if txn is None:
        raise ValueError("Transaction not found.")

    if conn.execute("SELECT id FROM payees WHERE id = ?", (payee_id,)).fetchone() is None:
        raise ValueError("Payee not found.")

    alias_text = txn["normalized_description"] or txn["raw_description"]

    conn.execute(
        """
        INSERT INTO payee_aliases (
            payee_id,
            alias,
            normalized_alias,
            source,
            usage_count,
            last_seen
        )
        VALUES (?, ?, ?, 'manual', 1, ?)
        ON CONFLICT(normalized_alias) DO UPDATE SET
            payee_id = excluded.payee_id,
            usage_count = usage_count + 1,
            last_seen = excluded.last_seen
        """,
        (
            payee_id,
            alias_text,
            extract_payee_key(alias_text),
            txn["posted_date"],
        ),
    )

    conn.execute(
        """
        UPDATE transactions
        SET payee_id = ?
        WHERE id = ?
        """,
        (payee_id, transaction_id),
    )

    return payee_id


def _set_override(conn, transaction_id: int, account_id: int):
    txn = conn.execute(
        """
        SELECT transaction_hash
        FROM transactions
        WHERE id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if txn is None:
        raise ValueError("Transaction not found.")

    OverrideRepository(conn).set(txn["transaction_hash"], account_id)
