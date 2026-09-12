import re

from intent_ledger.accounting.repositories.accounts import AccountRepository
from intent_ledger.accounting.repositories.payees import PayeeRepository
from intent_ledger.db import db
from intent_ledger.domain.models import Payee


def get_or_create_payee(conn, name: str, normalized_name: str, account_id: int) -> int:
    name = name.strip()
    valid_account_id = validate_account_id(conn, account_id)

    repo = PayeeRepository(conn)
    existing = repo.find_by_normalized_name(normalized_name)
    if existing:
        return existing.id

    return repo.create(name, normalized_name, valid_account_id).id


def validate_account_id(conn, account_id):
    if not isinstance(account_id, int):
        raise ValueError("Account ID must be an integer")

    account = AccountRepository(conn).get(account_id)
    if account is None:
        raise ValueError("Account ID not found")

    return account.id


def quick_create_payee(name: str, account_id: int | None = None) -> Payee:
    name = (name or "").strip()
    if not name:
        raise ValueError("Name is required.")

    normalized_name = normalize(name)

    with db.transaction() as conn:
        if account_id is not None:
            validate_account_id(conn, account_id)

        repo = PayeeRepository(conn)
        existing = repo.find_by_normalized_name(normalized_name)
        if existing:
            return existing

        return repo.create(name, normalized_name, account_id)


def normalize(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())
