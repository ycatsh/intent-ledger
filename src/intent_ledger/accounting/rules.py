import re

from intent_ledger.accounting.repositories.payees import PayeeRepository
from intent_ledger.accounting.repositories.rules import AccountRuleRepository
from intent_ledger.db import db
from intent_ledger.importer.normalize import extract_payee_key

MATCHERS = {
    "prefix": lambda text, pattern: text.startswith(pattern),
    "suffix": lambda text, pattern: text.endswith(pattern),
    "contains": lambda text, pattern: pattern in text,
    "equals": lambda text, pattern: text == pattern,
    "regex": lambda text, pattern: re.search(pattern, text) is not None,
}


# Rule-matching primitives:


def fetch_account_rules(conn):
    return AccountRuleRepository(conn).list_ordered()


def validate_pattern(match_type: str, pattern: str) -> None:
    if match_type == "regex":
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}") from e


def find_matching_rule(rules, raw_description: str):
    desc = raw_description.upper()
    fuzzy_desc = None

    for rule in rules:
        matcher = MATCHERS.get(rule["match_type"])
        if matcher is None:
            continue

        pattern = rule["pattern"].upper()

        if rule["match_type"] == "equals":
            if fuzzy_desc is None:
                fuzzy_desc = extract_payee_key(raw_description)
            text = fuzzy_desc
        else:
            text = desc

        try:
            if matcher(text, pattern):
                return rule

        except re.error:
            continue

    return None


def default_account_id(rules, conn, raw_description: str, payee_id):
    """Rule match, else the payee's default account, else the Unknown fallback."""
    rule = find_matching_rule(rules, raw_description)
    return (
        (rule["account_id"] if rule else None)
        or get_payee_account_id(conn, payee_id)
        or get_unknown_account_id(conn)
    )


def get_payee_account_id(conn, payee_id):
    if payee_id is None:
        return None

    payee = PayeeRepository(conn).get(payee_id)
    return payee.account_id if payee else None


def get_unknown_account_id(conn):
    row = conn.execute("""
        SELECT id
        FROM accounts
        WHERE name='Unknown'
    """).fetchone()

    if row is None:
        raise ValueError("Unknown fallback account does not exist")

    return row["id"]


def learn_account_rule(conn, normalized_description: str, account_id: int) -> None:
    repo = AccountRuleRepository(conn)
    repo.delete_learned(normalized_description)
    repo.create("equals", normalized_description, account_id, 100)


# Route-facing account rule CRUD:


def get_accounts():
    with db.transaction() as conn:
        return conn.execute("""
            SELECT id, name, type
            FROM accounts
            ORDER BY type
        """).fetchall()


def get_account_rules():
    with db.transaction() as conn:
        return AccountRuleRepository(conn).list_with_account_name()


def preview_rule_matches(match_type: str, pattern: str, limit: int = 200):
    if match_type not in MATCHERS:
        raise ValueError(f"unknown match type {match_type!r}")

    validate_pattern(match_type, pattern)

    candidate = {"match_type": match_type, "pattern": pattern}

    with db.transaction() as conn:
        rows = conn.execute("""
            SELECT
                t.id,
                t.raw_description,
                t.posted_date,
                t.amount_cents / 100.0 AS amount,
                a.name AS current_category
            FROM transactions t
            JOIN ledger l
                ON l.transaction_id = t.id AND l.account_id != t.account_id
            JOIN accounts a
                ON a.id = l.account_id
            ORDER BY t.posted_date DESC
        """).fetchall()

    matches = []
    for row in rows:
        if find_matching_rule([candidate], row["raw_description"]) is not None:
            matches.append(row)
            if len(matches) >= limit:
                break

    return matches


def get_rule(rule_id):
    with db.transaction() as conn:
        return AccountRuleRepository(conn).get(rule_id)


def update_account_rule(rule_id, form):
    pattern = form.get("pattern", "").strip()
    if not pattern:
        raise ValueError("Pattern is required.")

    match_type = form.get("match_type", "contains")
    validate_pattern(match_type, pattern)

    account_id = form.get("account_id", type=int)
    if not account_id:
        raise ValueError("Account is required.")

    with db.transaction() as conn:
        AccountRuleRepository(conn).update(
            rule_id,
            match_type,
            pattern,
            account_id,
            form.get("priority", 0, type=int),
            form.get("payee_id", type=int),
        )


def add_account_rule(form):
    pattern = form.get("pattern", "").strip()
    if not pattern:
        raise ValueError("Pattern is required.")

    match_type = form.get("match_type", "contains")
    validate_pattern(match_type, pattern)

    account_id = form.get("account_id", type=int)
    if not account_id:
        raise ValueError("Account is required.")

    with db.transaction() as conn:
        return AccountRuleRepository(conn).create(
            match_type,
            pattern,
            account_id,
            form.get("priority", 0, type=int),
            form.get("payee_id", type=int),
        )


def delete_account_rule(rule_id):
    with db.transaction() as conn:
        AccountRuleRepository(conn).delete(rule_id)
