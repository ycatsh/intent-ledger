from intent_ledger.accounting.ledger import rebuild_ledger


def group_totals(conn):
    rows = conn.execute(
        "SELECT group_id, SUM(amount_cents) AS balance FROM ledger GROUP BY group_id"
    ).fetchall()
    return {row["group_id"]: row["balance"] for row in rows}


def legs_for_description(conn, description):
    return conn.execute(
        """
        SELECT l.account_id, l.amount_cents, l.group_id, t.posted_date
        FROM ledger l
        JOIN transactions t ON t.id = l.transaction_id
        WHERE l.group_id IN (
            SELECT own.group_id
            FROM ledger own
            JOIN transactions mine ON mine.id = own.transaction_id AND mine.account_id = own.account_id
            WHERE mine.raw_description = ?
        )
        ORDER BY l.id
        """,
        (description,),
    ).fetchall()


def ledger_rows(conn):
    return conn.execute(
        "SELECT group_id, transaction_id, account_id, amount_cents FROM ledger ORDER BY id"
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


def test_each_transfer_leg_posts_under_its_own_transaction_and_date(
    conn, account_factory, transaction_factory, transfer_rule_factory
):
    checking = account_factory("Test Checking")
    savings = account_factory("Test Savings")
    transfer_rule_factory("MOVE")

    transaction_factory(checking, "2026-01-31", -5000, "MOVE TO SAVINGS")
    transaction_factory(savings, "2026-02-01", 5000, "MOVE FROM CHECKING")

    rebuild_ledger()

    legs = {leg["account_id"]: leg for leg in legs_for_description(conn, "MOVE TO SAVINGS")}
    assert legs[checking]["posted_date"] == "2026-01-31"
    assert legs[savings]["posted_date"] == "2026-02-01"
    assert legs[checking]["group_id"] == legs[savings]["group_id"]


def test_rebuilding_twice_gives_the_same_ledger(
    conn, account_factory, transaction_factory, transfer_rule_factory
):
    checking = account_factory("Test Checking")
    savings = account_factory("Test Savings")
    transfer_rule_factory("MOVE")
    transaction_factory(checking, "2026-01-05", -450, "COFFEE SHOP")
    transaction_factory(checking, "2026-01-06", -5000, "MOVE TO SAVINGS")
    transaction_factory(savings, "2026-01-06", 5000, "MOVE TO SAVINGS")

    rebuild_ledger()
    first = ledger_rows(conn)
    rebuild_ledger()

    assert ledger_rows(conn) == first


def test_every_transaction_posts_to_its_own_account_exactly_once(
    conn, account_factory, transaction_factory, transfer_rule_factory
):
    checking = account_factory("Test Checking")
    savings = account_factory("Test Savings")
    transfer_rule_factory("MOVE")
    for day in range(1, 6):
        transaction_factory(checking, f"2026-01-0{day}", -100 * day, f"SHOP {day}")
    transaction_factory(checking, "2026-01-06", -5000, "MOVE TO SAVINGS")
    transaction_factory(savings, "2026-01-07", 5000, "MOVE TO SAVINGS")

    rebuild_ledger()

    own_lines = conn.execute("""
        SELECT t.id, COUNT(l.id) AS lines
        FROM transactions t
        LEFT JOIN ledger l ON l.transaction_id = t.id AND l.account_id = t.account_id
        GROUP BY t.id
    """).fetchall()
    assert all(row["lines"] == 1 for row in own_lines)
    assert all(balance == 0 for balance in group_totals(conn).values())


def test_a_regex_rule_can_use_character_classes(conn, account_factory, transaction_factory):
    checking = account_factory("Test Checking")
    rent = account_factory("Test Rent", type="expense")
    transaction_factory(checking, "2026-01-05", -90000, "upi/12345/landlord")
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id) VALUES ('regex', ?, ?)",
        (r"^UPI/\d+/LANDLORD$", rent),
    )

    rebuild_ledger()

    assert {leg["account_id"] for leg in legs_for_description(conn, "upi/12345/landlord")} == {checking, rent}


def test_a_contains_rule_matches_the_description_or_its_payee_key(conn, account_factory, transaction_factory):
    checking = account_factory("Test Checking")
    loans = account_factory("Test Loan", type="expense")
    transaction_factory(checking, "2026-01-05", -50000, "FIRST/NATIONAL/HOME/LOAN/12345")
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id) "
        "VALUES ('contains', 'FIRST NATIONAL HOME', ?)",
        (loans,),
    )

    rebuild_ledger()

    assert {leg["account_id"] for leg in legs_for_description(conn, "FIRST/NATIONAL/HOME/LOAN/12345")} == {
        checking,
        loans,
    }
