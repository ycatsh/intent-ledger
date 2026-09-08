from intent_ledger.accounting.repositories.counterparties import CounterpartyRepository
from intent_ledger.db import db


def get_all_counterparties():
    with db.transaction() as conn:
        return CounterpartyRepository(conn).list_all_ordered()


def get_counterparty_id_by_name(name: str) -> int | None:
    with db.transaction() as conn:
        return CounterpartyRepository(conn).get_id_by_name((name or "").strip())


def assign_counterparty(transaction_hashes: list[str], counterparty_id: int | None):
    if not transaction_hashes:
        raise ValueError("Select at least one transaction.")

    with db.transaction() as conn:
        repo = CounterpartyRepository(conn)
        if counterparty_id is not None and repo.get(counterparty_id) is None:
            raise ValueError("Counterparty not found.")

        repo.assign_to_transactions(transaction_hashes, counterparty_id)
