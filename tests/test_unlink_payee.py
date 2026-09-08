import pytest

from intent_ledger.accounting.mappings import unlink_payee
from intent_ledger.accounting.payees import get_or_create_payee
from intent_ledger.accounting.rules_overrides import get_override_account_id


def test_unlink_payee_preserves_effective_account_for_every_transaction(
    conn, account_factory, transaction_factory
):
    checking_id = account_factory("Test Checking")
    bank_default_id = account_factory("Test Miscellaneous", type="expense")
    edu_loan_id = account_factory("Test Edu Loan", type="liability")

    payee_id = get_or_create_payee(conn, "First National", "firstnational", bank_default_id)
    conn.commit()

    # Transaction with an existing override to a specific loan account
    # must keep resolving to that same account after unlink.
    overridden_hash = transaction_factory(
        checking_id, "2026-01-10", -50000, "FIRST NATIONAL HOME LOAN PAYMENT"
    )
    txn_overridden = conn.execute(
        "SELECT id FROM transactions WHERE transaction_hash = ?", (overridden_hash,)
    ).fetchone()["id"]
    conn.execute("UPDATE transactions SET payee_id = ? WHERE id = ?", (payee_id, txn_overridden))
    conn.execute(
        "INSERT INTO transactions_overrides (transaction_hash, account_id) VALUES (?, ?)",
        (overridden_hash, edu_loan_id),
    )
    conn.commit()

    # Transaction with no override that is relying purely on the payee's
    # default account. That must be snapshotted into an override so it
    # still resolves the same way once the payee is gone.
    bare_hash = transaction_factory(checking_id, "2026-01-12", -1000, "FIRST NATIONAL SOMETHING ELSE")
    txn_bare = conn.execute(
        "SELECT id FROM transactions WHERE transaction_hash = ?", (bare_hash,)
    ).fetchone()["id"]
    conn.execute("UPDATE transactions SET payee_id = ? WHERE id = ?", (payee_id, txn_bare))
    conn.commit()

    before_overridden = get_override_account_id(conn, overridden_hash) or bank_default_id
    before_bare = get_override_account_id(conn, bare_hash) or bank_default_id

    summary = unlink_payee("first national")

    assert summary["transactions_unlinked"] == 2
    assert summary["overrides_created"] == 1
    assert summary["aliases_deleted"] == 0

    after_overridden_txn = conn.execute(
        "SELECT payee_id FROM transactions WHERE id = ?", (txn_overridden,)
    ).fetchone()
    after_bare_txn = conn.execute("SELECT payee_id FROM transactions WHERE id = ?", (txn_bare,)).fetchone()
    assert after_overridden_txn["payee_id"] is None
    assert after_bare_txn["payee_id"] is None

    after_overridden = get_override_account_id(conn, overridden_hash)
    after_bare = get_override_account_id(conn, bare_hash)

    assert after_overridden == before_overridden == edu_loan_id
    assert after_bare == before_bare == bank_default_id

    remaining_payee = conn.execute("SELECT id FROM payees WHERE canonical_name = 'First National'").fetchone()
    assert remaining_payee is None


def test_unlink_payee_deletes_aliases(conn, account_factory, transaction_factory):
    default_id = account_factory("Test Misc", type="expense")
    payee_id = get_or_create_payee(conn, "First National", "firstnational", default_id)
    conn.execute(
        """
        INSERT INTO payee_aliases (payee_id, alias, normalized_alias, source, usage_count)
        VALUES (?, 'FIRST NATIONAL RAW', 'firstnationalraw', 'auto', 1)
        """,
        (payee_id,),
    )
    conn.commit()

    summary = unlink_payee("First National")

    assert summary["aliases_deleted"] == 1
    remaining = conn.execute("SELECT id FROM payee_aliases WHERE payee_id = ?", (payee_id,)).fetchone()
    assert remaining is None


def test_unlink_payee_leaves_split_transactions_alone(conn, account_factory, transaction_factory):
    checking_id = account_factory("Test Checking")
    groceries_id = account_factory("Test Groceries", type="expense")
    dining_id = account_factory("Test Dining", type="expense")

    payee_id = get_or_create_payee(conn, "First National", "firstnational", groceries_id)
    conn.commit()

    split_hash = transaction_factory(checking_id, "2026-01-15", -2000, "FIRST NATIONAL MIXED SPEND")
    txn_split = conn.execute(
        "SELECT id FROM transactions WHERE transaction_hash = ?", (split_hash,)
    ).fetchone()["id"]
    conn.execute("UPDATE transactions SET payee_id = ? WHERE id = ?", (payee_id, txn_split))
    conn.execute(
        """
        INSERT INTO transactions_splits (transaction_hash, account_id, amount_cents, note)
        VALUES (?, ?, 1200, 'g'), (?, ?, 800, 'd')
        """,
        (split_hash, groceries_id, split_hash, dining_id),
    )
    conn.commit()

    summary = unlink_payee("First National")

    # A split already fully determines this transaction's category
    # independent of payee_id, so unlinking must not manufacture an
    # override on top of it
    assert summary["transactions_unlinked"] == 1
    assert summary["overrides_created"] == 0

    assert get_override_account_id(conn, split_hash) is None

    remaining_splits = conn.execute(
        "SELECT account_id, amount_cents, note FROM transactions_splits "
        "WHERE transaction_hash = ? ORDER BY account_id",
        (split_hash,),
    ).fetchall()
    assert [dict(r) for r in remaining_splits] == [
        {"account_id": groceries_id, "amount_cents": 1200, "note": "g"},
        {"account_id": dining_id, "amount_cents": 800, "note": "d"},
    ]


def test_unlink_payee_raises_for_unknown_payee(conn):
    with pytest.raises(ValueError):
        unlink_payee("Does Not Exist")
