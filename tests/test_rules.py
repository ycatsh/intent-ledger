from intent_ledger.accounting.rules import add_account_rule, get_rule, update_account_rule


class FakeForm(dict):
    def get(self, key, default=None, type=None):
        if key not in self:
            return default
        value = super().get(key)
        if type is None:
            return value
        try:
            return type(value)
        except (TypeError, ValueError):
            return default


def test_add_account_rule_without_a_payee(conn, account_factory):
    groceries_id = account_factory("Test Groceries", type="expense")

    rule_id = add_account_rule(
        FakeForm(match_type="contains", pattern="TRADER JOES", account_id=groceries_id, priority=0)
    )

    rule = get_rule(rule_id)
    assert rule["account_id"] == groceries_id
    assert rule["payee_id"] is None
    assert rule["payee_name"] is None


def test_add_account_rule_with_an_existing_payee(conn, account_factory):
    groceries_id = account_factory("Test Groceries", type="expense")
    payee_id = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) "
        "VALUES ('Trader Joes', 'traderjoes', ?)",
        (groceries_id,),
    ).lastrowid
    conn.commit()

    rule_id = add_account_rule(
        FakeForm(
            match_type="contains",
            pattern="TRADER JOES",
            account_id=groceries_id,
            priority=0,
            payee_id=payee_id,
        )
    )

    rule = get_rule(rule_id)
    assert rule["payee_id"] == payee_id
    assert rule["payee_name"] == "Trader Joes"


def test_update_account_rule_can_add_and_clear_a_payee(conn, account_factory):
    groceries_id = account_factory("Test Groceries", type="expense")
    payee_id = conn.execute(
        "INSERT INTO payees (canonical_name, normalized_name, account_id) "
        "VALUES ('Trader Joes', 'traderjoes', ?)",
        (groceries_id,),
    ).lastrowid
    conn.commit()

    rule_id = add_account_rule(
        FakeForm(match_type="contains", pattern="TRADER JOES", account_id=groceries_id, priority=0)
    )

    update_account_rule(
        rule_id,
        FakeForm(
            match_type="contains",
            pattern="TRADER JOES",
            account_id=groceries_id,
            priority=0,
            payee_id=payee_id,
        ),
    )
    assert get_rule(rule_id)["payee_id"] == payee_id

    update_account_rule(
        rule_id,
        FakeForm(match_type="contains", pattern="TRADER JOES", account_id=groceries_id, priority=0),
    )
    assert get_rule(rule_id)["payee_id"] is None
