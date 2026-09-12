"""Resolution ladder: how the account for each transaction is picked.

  1. Transfer match:   if there are two transaction legs 
  2. Manual split:     if the user added a manual split to any transaction
  3. Manual override:  if the user added a manual override to the resolved transaction
  4. Matching rule:    account returned by the user configured rules
  5. Payee default:    account assigned as the default for any given payee
  6. Unknown fallback 
"""

from dataclasses import dataclass

from intent_ledger.accounting.repositories.rules_splits import SplitRepository
from intent_ledger.accounting.rules import default_account_id
from intent_ledger.accounting.rules_overrides import get_override_account_id

SKIP = None
"""Returned when a transfer counterpart already covers this transaction."""


@dataclass(frozen=True)
class CounterEntry:
    account_id: int
    amount_cents: int
    description: str


def resolve(conn, txn, rules, transfer_pairs):
    counterpart = transfer_pairs.get(txn["id"])
    if counterpart is not None:
        return _resolve_transfer(txn, counterpart)

    splits = SplitRepository(conn).list_for_transaction(txn["transaction_hash"])
    if splits:
        return _resolve_splits(txn, splits)

    override_account_id = get_override_account_id(conn, txn["transaction_hash"])
    if override_account_id is not None:
        return _resolve_to_account(txn, override_account_id)

    return _resolve_to_account(txn, default_account_id(rules, conn, txn["raw_description"], txn["payee_id"]))


def _resolve_transfer(txn, counterpart):
    if txn["amount_cents"] > 0:
        return SKIP

    return [CounterEntry(counterpart["account_id"], -txn["amount_cents"], txn["raw_description"])]


def _resolve_splits(txn, splits):
    return [
        CounterEntry(split["account_id"], split["amount_cents"], split["note"] or txn["raw_description"])
        for split in splits
    ]


def _resolve_to_account(txn, account_id):
    return [CounterEntry(account_id, -txn["amount_cents"], txn["raw_description"])]
