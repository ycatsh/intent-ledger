import re
from functools import cached_property

from intent_ledger import forms
from intent_ledger.accounting.repositories.accounts import AccountRepository
from intent_ledger.accounting.repositories.payees import PayeeRepository
from intent_ledger.accounting.repositories.rules import AccountRuleRepository
from intent_ledger.db import db
from intent_ledger.importer.normalize import extract_payee_key

LEARNED_PRIORITY = 50

MATCHERS = {
    "prefix": lambda text, pattern: text.upper.startswith(pattern.upper()),
    "suffix": lambda text, pattern: text.upper.endswith(pattern.upper()),
    "contains": lambda text, pattern: pattern.upper() in text.upper or pattern.upper() in text.payee_key,
    "equals": lambda text, pattern: text.payee_key == pattern.upper(),
    "regex": lambda text, pattern: re.search(pattern, text.raw, re.IGNORECASE) is not None,
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
    text = _Description(raw_description)

    for rule in rules:
        matcher = MATCHERS.get(rule["match_type"])
        if matcher is None:
            continue

        try:
            if matcher(text, rule["pattern"]):
                return rule

        except re.error:
            continue

    return None


class _Description:
    def __init__(self, raw: str):
        self.raw = raw
        self.upper = raw.upper()

    @cached_property
    def payee_key(self) -> str:
        return extract_payee_key(self.raw)


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
        WHERE role = 'unknown'
    """).fetchone()

    if row is None:
        raise ValueError("Unknown fallback account does not exist")

    return row["id"]


def learn_account_rule(conn, pattern: str, account_id: int) -> None:
    repo = AccountRuleRepository(conn)
    repo.delete_learned(pattern, LEARNED_PRIORITY)
    repo.create("contains", pattern, account_id, LEARNED_PRIORITY, needs_review=True)


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


def get_rules_needing_review_count() -> int:
    with db.transaction() as conn:
        return AccountRuleRepository(conn).needs_review_count()


def dismiss_rule_review(rule_id: int) -> None:
    with db.transaction() as conn:
        AccountRuleRepository(conn).mark_reviewed(rule_id)


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
            JOIN ledger own
                ON own.transaction_id = t.id AND own.account_id = t.account_id
            JOIN ledger l
                ON l.group_id = own.group_id AND l.account_id != t.account_id
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
    with db.transaction() as conn:
        AccountRuleRepository(conn).update(rule_id, *_parse_rule_form(conn, form))


def add_account_rule(form):
    with db.transaction() as conn:
        return AccountRuleRepository(conn).create(*_parse_rule_form(conn, form))


def _parse_rule_form(conn, form):
    pattern = forms.text(form, "pattern")
    if not pattern:
        raise ValueError("Pattern is required.")

    match_type = forms.choice(form, "match_type", MATCHERS, "match type")
    validate_pattern(match_type, pattern)

    account_id = forms.integer(form, "account_id", "account")
    payee_id = forms.optional_integer(form, "payee_id", "payee")

    if AccountRepository(conn).get(account_id) is None:
        raise ValueError("Account not found.")
    if payee_id is not None and PayeeRepository(conn).get(payee_id) is None:
        raise ValueError("Payee not found.")

    return (
        match_type,
        pattern,
        account_id,
        forms.optional_integer(form, "priority", "priority") or 0,
        payee_id,
    )


def delete_account_rule(rule_id):
    with db.transaction() as conn:
        AccountRuleRepository(conn).delete(rule_id)
