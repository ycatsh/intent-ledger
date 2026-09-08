from intent_ledger.accounting.ledger import rebuild_ledger


def group_totals(conn):
    rows = conn.execute(
        "SELECT group_id, SUM(amount_cents) AS balance FROM ledger GROUP BY group_id"
    ).fetchall()
    return {row["group_id"]: row["balance"] for row in rows}


def legs_for_description(conn, description):
    return conn.execute(
        "SELECT account_id, amount_cents, group_id FROM ledger WHERE description = ?",
        (description,),
    ).fetchall()


def test_every_group_balances_to_zero(conn, account_factory, transaction_factory):
    checking = account_factory("Test Checking")
    transaction_factory(checking, "2026-01-05", -450, "COFFEE SHOP")

    rebuild_ledger()

    assert all(balance == 0 for balance in group_totals(conn).values())


def test_unmatched_transaction_falls_back_to_unknown(conn, account_factory, transaction_factory):
    checking = account_factory("Test Checking")
    transaction_factory(checking, "2026-01-05", -450, "COFFEE SHOP")

    rebuild_ledger()

    unknown_id = conn.execute("SELECT id FROM accounts WHERE name = 'Unknown'").fetchone()["id"]
    legs = legs_for_description(conn, "COFFEE SHOP")

    assert {leg["account_id"] for leg in legs} == {checking, unknown_id}


def test_matching_transfer_pair_is_linked_as_one_group(
    conn, account_factory, transaction_factory, transfer_rule_factory
):
    checking = account_factory("Test Checking")
    savings = account_factory("Test Savings")
    transfer_rule_factory("TRANSFER")

    transaction_factory(checking, "2026-01-05", -10000, "TRANSFER TO SAVINGS")
    transaction_factory(savings, "2026-01-05", 10000, "TRANSFER TO SAVINGS")

    rebuild_ledger()

    legs = legs_for_description(conn, "TRANSFER TO SAVINGS")
    assert len(legs) == 2
    assert legs[0]["group_id"] == legs[1]["group_id"]
    assert {leg["account_id"] for leg in legs} == {checking, savings}


def test_transfer_pair_without_a_matching_rule_is_not_linked(conn, account_factory, transaction_factory):
    checking = account_factory("Test Checking")
    savings = account_factory("Test Savings")

    transaction_factory(checking, "2026-01-05", -10000, "TRANSFER TO SAVINGS")
    transaction_factory(savings, "2026-01-05", 10000, "TRANSFER TO SAVINGS")

    rebuild_ledger()

    legs = legs_for_description(conn, "TRANSFER TO SAVINGS")
    unknown_id = conn.execute("SELECT id FROM accounts WHERE name = 'Unknown'").fetchone()["id"]

    assert {leg["account_id"] for leg in legs} == {checking, savings, unknown_id}


def test_same_day_same_amount_very_different_description_is_not_a_transfer(
    conn, account_factory, transaction_factory, transfer_rule_factory
):
    checking = account_factory("Test Checking")
    savings = account_factory("Test Savings")
    transfer_rule_factory("WITHDRAWAL")
    transfer_rule_factory("DEPOSIT")

    transaction_factory(checking, "2026-01-05", -10000, "WITHDRAWAL")
    transaction_factory(savings, "2026-01-05", 10000, "DEPOSIT")

    rebuild_ledger()

    withdrawal_legs = legs_for_description(conn, "WITHDRAWAL")
    deposit_legs = legs_for_description(conn, "DEPOSIT")

    assert len(withdrawal_legs) == 2
    assert len(deposit_legs) == 2
    assert withdrawal_legs[0]["group_id"] != deposit_legs[0]["group_id"]


def test_cross_month_transfer_with_matching_description_links(
    conn, account_factory, transaction_factory, transfer_rule_factory
):
    checking = account_factory("Test Checking")
    savings = account_factory("Test Savings")
    transfer_rule_factory("MOVE")

    transaction_factory(checking, "2026-01-31", -5000, "MOVE TO SAVINGS")
    transaction_factory(savings, "2026-02-01", 5000, "MOVE TO SAVINGS")

    rebuild_ledger()

    legs = legs_for_description(conn, "MOVE TO SAVINGS")
    assert {leg["account_id"] for leg in legs} == {checking, savings}


def test_multiple_same_amount_same_day_transfers_still_balance(
    conn, account_factory, transaction_factory, transfer_rule_factory
):
    checking = account_factory("Test Checking")
    savings = account_factory("Test Savings")
    transfer_rule_factory("MOVE")

    for _ in range(3):
        transaction_factory(checking, "2026-01-05", -1000, "MOVE")
    for _ in range(3):
        transaction_factory(savings, "2026-01-05", 1000, "MOVE")

    rebuild_ledger()

    assert all(balance == 0 for balance in group_totals(conn).values())


def test_rebuild_ledger_returns_how_many_transactions_changed_category(
    conn, account_factory, transaction_factory
):
    checking = account_factory("Test Checking")
    groceries = account_factory("Test Groceries", type="expense")
    transaction_factory(checking, "2026-01-05", -450, "COFFEE SHOP")

    first_pass = rebuild_ledger()
    assert first_pass == 1  # Unknown -> nothing before, categorized now

    unchanged_pass = rebuild_ledger()
    assert unchanged_pass == 0  # nothing about the transaction changed

    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id, priority) "
        "VALUES ('equals', 'COFFEE SHOP', ?, 0)",
        (groceries,),
    )
    conn.commit()

    recategorized_pass = rebuild_ledger()
    assert recategorized_pass == 1  # the new rule redirects it away from Unknown


def test_matching_rule_backfills_a_payee_when_the_transaction_has_none(
    conn, account_factory, transaction_factory
):
    checking = account_factory("Test Checking")
    groceries = account_factory("Test Groceries", type="expense")
    payee_id = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) "
        "VALUES ('Trader Joes', 'traderjoes', ?)",
        (groceries,),
    ).lastrowid
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id, payee_id, priority) "
        "VALUES ('equals', 'TRADER JOES', ?, ?, 0)",
        (groceries, payee_id),
    )
    conn.commit()

    transaction_hash = transaction_factory(checking, "2026-01-05", -4500, "TRADER JOES")
    rebuild_ledger()

    txn = conn.execute(
        "SELECT payee_id FROM transactions WHERE transaction_hash = ?", (transaction_hash,)
    ).fetchone()
    assert txn["payee_id"] == payee_id


def test_changing_a_rules_payee_repoints_already_backfilled_transactions(
    conn, account_factory, transaction_factory
):
    checking = account_factory("Test Checking")
    groceries = account_factory("Test Groceries", type="expense")
    old_payee_id = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) "
        "VALUES ('Trader Joes', 'traderjoes', ?)",
        (groceries,),
    ).lastrowid
    new_payee_id = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) "
        "VALUES ('TJ Groceries', 'tjgroceries', ?)",
        (groceries,),
    ).lastrowid
    rule_id = conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id, payee_id, priority) "
        "VALUES ('equals', 'TRADER JOES', ?, ?, 0)",
        (groceries, old_payee_id),
    ).lastrowid
    conn.commit()

    transaction_hash = transaction_factory(checking, "2026-01-05", -4500, "TRADER JOES")
    rebuild_ledger()
    before = conn.execute(
        "SELECT payee_id FROM transactions WHERE transaction_hash = ?", (transaction_hash,)
    ).fetchone()
    assert before["payee_id"] == old_payee_id

    conn.execute("UPDATE account_rules SET payee_id = ? WHERE id = ?", (new_payee_id, rule_id))
    conn.commit()
    rebuild_ledger()

    after = conn.execute(
        "SELECT payee_id FROM transactions WHERE transaction_hash = ?", (transaction_hash,)
    ).fetchone()
    assert after["payee_id"] == new_payee_id


def test_a_rules_payee_overrides_an_alias_or_manually_set_payee(conn, account_factory, transaction_factory):
    """Tests the resolution ladder"""
    checking = account_factory("Test Checking")
    groceries = account_factory("Test Groceries", type="expense")
    rule_payee_id = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) "
        "VALUES ('Trader Joes', 'traderjoes', ?)",
        (groceries,),
    ).lastrowid
    existing_payee_id = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) VALUES ('My Alias', 'myalias', ?)",
        (groceries,),
    ).lastrowid
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id, payee_id, priority) "
        "VALUES ('equals', 'TRADER JOES', ?, ?, 0)",
        (groceries, rule_payee_id),
    )
    conn.commit()

    transaction_hash = transaction_factory(checking, "2026-01-05", -4500, "TRADER JOES")
    conn.execute(
        "UPDATE transactions SET payee_id = ? WHERE transaction_hash = ?",
        (existing_payee_id, transaction_hash),
    )
    conn.commit()

    rebuild_ledger()

    txn = conn.execute(
        "SELECT payee_id FROM transactions WHERE transaction_hash = ?", (transaction_hash,)
    ).fetchone()
    assert txn["payee_id"] == rule_payee_id


def test_a_rule_with_no_payee_leaves_an_existing_payee_alone(conn, account_factory, transaction_factory):
    checking = account_factory("Test Checking")
    groceries = account_factory("Test Groceries", type="expense")
    existing_payee_id = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) VALUES ('My Alias', 'myalias', ?)",
        (groceries,),
    ).lastrowid
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id, priority) "
        "VALUES ('equals', 'TRADER JOES', ?, 0)",
        (groceries,),
    )
    conn.commit()

    transaction_hash = transaction_factory(checking, "2026-01-05", -4500, "TRADER JOES")
    conn.execute(
        "UPDATE transactions SET payee_id = ? WHERE transaction_hash = ?",
        (existing_payee_id, transaction_hash),
    )
    conn.commit()

    rebuild_ledger()

    txn = conn.execute(
        "SELECT payee_id FROM transactions WHERE transaction_hash = ?", (transaction_hash,)
    ).fetchone()
    assert txn["payee_id"] == existing_payee_id


def test_differently_worded_transfer_legs_still_link(
    conn, account_factory, transaction_factory, transfer_rule_factory
):
    checking = account_factory("Test Checking")
    credit_card = account_factory("Test Credit Card", type="liability")
    transfer_rule_factory("PAYMENT")

    transaction_factory(checking, "2026-01-20", -18000, "ONLINE PAYMENT TO CREDIT CARD")
    transaction_factory(credit_card, "2026-01-21", 18000, "PAYMENT RECEIVED - THANK YOU")

    rebuild_ledger()

    legs = legs_for_description(conn, "ONLINE PAYMENT TO CREDIT CARD")
    assert {leg["account_id"] for leg in legs} == {checking, credit_card}
