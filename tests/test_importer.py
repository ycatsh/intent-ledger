import pytest

from intent_ledger.accounting.inbox import _assign
from intent_ledger.importer.importer import import_statement


def write_csv(path, text):
    path.write_text(text)
    return path


@pytest.fixture
def checking_account(account_factory):
    return account_factory("Test Checking")


def test_import_inserts_transactions(tmp_path, checking_account):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit,balance\n"
        "2026-01-05,COFFEE SHOP,4.50,,995.50\n"
        "2026-01-06,PAYCHECK,,1500.00,2495.50\n",
    )

    summary = import_statement(path, account_id=checking_account)

    assert summary["inserted"] == 2
    assert summary["duplicates"] == 0


def test_reimporting_same_file_is_deduped(tmp_path, conn, checking_account):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit,balance\n2026-01-05,COFFEE SHOP,4.50,,995.50\n",
    )

    first = import_statement(path, account_id=checking_account)
    second = import_statement(path, account_id=checking_account)

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["duplicates"] == 1


def test_same_day_same_amount_different_description_are_not_deduped(tmp_path, conn, checking_account):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit,balance\n"
        "2026-01-05,COFFEE SHOP,4.50,,995.50\n"
        "2026-01-05,BOOKSTORE,4.50,,995.50\n",
    )

    summary = import_statement(path, account_id=checking_account)

    assert summary["inserted"] == 2
    assert summary["duplicates"] == 0


def test_import_against_unknown_account_raises(tmp_path, conn):
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit,balance\n2026-01-05,COFFEE SHOP,4.50,,995.50\n",
    )

    with pytest.raises(ValueError):
        import_statement(path, account_id=999999)


def test_categorizing_one_transaction_resolves_a_later_noisy_variant_via_alias(
    tmp_path, conn, checking_account, account_factory
):
    """Regression test for the alias-key stub: the same merchant with a
    different reference-number each time must still resolve via the
    stage-3 exact-alias lookup, not just via fuzzy grouping in the inbox UI.
    """
    account_factory("Test Groceries", type="expense")

    first_desc = (
        "PAY/Greenleaf/paynet-112514809/PAY/PRAIRIE BANK/645594840922/REFcbc1df2365d41f5975fee1fbd0fc1d2/"
    )
    second_desc = (
        "PAY/Greenleaf/paynet-112514809/PAY/PRAIRIE BANK/608556257181/REFa5a36629675f4bc5bc7c8c313ef67d33/"
    )

    import_statement(
        write_csv(
            tmp_path / "first.csv",
            f"date,description,withdrawal,deposit,balance\n2026-01-05,{first_desc},4.50,,995.50\n",
        ),
        account_id=checking_account,
    )
    transaction_id = conn.execute(
        "SELECT id FROM transactions WHERE raw_description = ?", (first_desc,)
    ).fetchone()["id"]
    _assign(conn, transaction_id, "Greenleaf", "Test Groceries")
    conn.commit()

    import_statement(
        write_csv(
            tmp_path / "second.csv",
            f"date,description,withdrawal,deposit,balance\n2026-01-06,{second_desc},6.25,,989.25\n",
        ),
        account_id=checking_account,
    )

    resolved = conn.execute(
        "SELECT payee_id FROM transactions WHERE raw_description = ?", (second_desc,)
    ).fetchone()
    payee = conn.execute("SELECT id FROM payees WHERE canonical_name = 'Greenleaf'").fetchone()

    assert resolved["payee_id"] == payee["id"]


def test_import_against_inactive_account_raises(tmp_path, conn, account_factory):
    inactive_id = account_factory("Closed Account", is_active=0)
    path = write_csv(
        tmp_path / "statement.csv",
        "date,description,withdrawal,deposit,balance\n2026-01-05,COFFEE SHOP,4.50,,995.50\n",
    )

    with pytest.raises(ValueError):
        import_statement(path, account_id=inactive_id)
