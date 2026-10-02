import pytest
from werkzeug.datastructures import MultiDict

from intent_ledger.accounting.budget import (
    delete_goal,
    get_budget_page,
    move_budget,
    save_goal,
)
from intent_ledger.accounting.ledger import rebuild_ledger


class FakeForm(MultiDict):
    def __init__(self, values=None, **fields):
        fields = {**(values or {}), **fields}
        super().__init__(
            [
                (key, str(item))
                for key, value in fields.items()
                for item in (value if isinstance(value, list) else [value])
                if item is not None
            ]
        )


@pytest.fixture
def category(conn, account_factory):
    def make(name, parent_account_id=None):
        return account_factory(name, type="expense", budget=1, parent_account_id=parent_account_id)

    return make


def set_budget(conn, account_id, period, amount_cents):
    conn.execute(
        "INSERT INTO budgets (account_id, period, amount_cents) VALUES (?, ?, ?) "
        "ON CONFLICT(account_id, period) DO UPDATE SET amount_cents = excluded.amount_cents",
        (account_id, period, amount_cents),
    )
    conn.commit()


def row_for(page, name):
    return next(row for row in page["rows"] if row["name"] == name)


def test_left_is_assigned_plus_spend_for_the_month(conn, category):
    groceries = category("Test Groceries")
    set_budget(conn, groceries, "2026-03-01", 50000)

    page = get_budget_page(2026, 3)
    cell = row_for(page, "Test Groceries")["by_period"]["2026-03-01"]

    assert cell["assigned"] == 500.0
    assert cell["left"] == 500.0


def test_carry_in_excludes_the_current_month(conn, category):
    groceries = category("Test Groceries")
    set_budget(conn, groceries, "2026-01-01", 10000)
    set_budget(conn, groceries, "2026-02-01", 10000)
    set_budget(conn, groceries, "2026-03-01", 10000)

    page = get_budget_page(2026, 3)
    row = row_for(page, "Test Groceries")

    assert row["rollover"] == 300.0
    assert row["carry_in"] == 200.0


def test_rollover_starts_at_the_first_budgeted_period(conn, category):
    groceries = category("Test Groceries")
    set_budget(conn, groceries, "2026-02-01", 10000)

    page = get_budget_page(2026, 3)
    row = row_for(page, "Test Groceries")

    assert row["rollover"] == 100.0
    assert row["carry_in"] == 100.0


def test_categories_group_by_parent_account(conn, account_factory, category):
    parent = account_factory("Living", type="expense")
    category("Rent", parent_account_id=parent)
    category("Test Groceries", parent_account_id=parent)
    category("Fun")

    page = get_budget_page(2026, 3)
    groups = {group["name"]: group for group in page["groups"]}

    assert set(groups) == {"Living", "Ungrouped"}
    assert len(groups["Living"]["row_ids"]) == 2


def test_group_subtotals_sum_their_rows(conn, account_factory, category):
    parent = account_factory("Living", type="expense")
    rent = category("Rent", parent_account_id=parent)
    groceries = category("Test Groceries", parent_account_id=parent)
    set_budget(conn, rent, "2026-03-01", 120000)
    set_budget(conn, groceries, "2026-03-01", 50000)

    page = get_budget_page(2026, 3)
    living = next(group for group in page["groups"] if group["name"] == "Living")

    assert living["by_period"]["2026-03-01"]["assigned"] == 1700.0


def test_to_budget_subtracts_assigned_from_income(conn, account_factory, category, transaction_factory):
    checking = account_factory("Test Checking")
    account_factory("Salary", type="income", budget=1)
    groceries = category("Test Groceries")
    set_budget(conn, groceries, "2026-03-01", 50000)

    transaction_factory(checking, "2026-03-05", 300000, "PAYROLL")
    rebuild_ledger()

    page = get_budget_page(2026, 3)

    assert page["to_budget"] == page["income"] - 500.0 - page["unbudgeted_spending"]


def test_trend_covers_six_months_ending_on_the_primary_period(conn, category):
    category("Test Groceries")

    page = get_budget_page(2026, 3)

    assert page["trend_periods"][0] == "2025-10-01"
    assert page["trend_periods"][-1] == "2026-03-01"
    assert len(row_for(page, "Test Groceries")["trend"]["amounts"]) == 6


def test_last_and_avg3_presets_come_from_prior_months(conn, category):
    groceries = category("Test Groceries")
    set_budget(conn, groceries, "2026-02-01", 40000)

    page = get_budget_page(2026, 3)
    presets = row_for(page, "Test Groceries")["presets"]

    assert presets["last"] == 400.0
    assert presets["avg3"] == 0.0


def test_goal_preset_spreads_the_remainder_over_months_left(conn, category):
    laptop = category("Laptop")
    set_budget(conn, laptop, "2026-03-01", 20000)

    save_goal(FakeForm({f"goal_amount_{laptop}": "800", f"goal_date_{laptop}": "2026-05-01"}), laptop)

    page = get_budget_page(2026, 3)
    row = row_for(page, "Laptop")

    assert row["goal"]["target"] == 800.0
    assert row["presets"]["goal"] == 266.67


def test_the_goal_suggestion_does_not_move_when_you_assign_it(conn, category):
    laptop = category("Laptop")
    save_goal(FakeForm({f"goal_amount_{laptop}": "900", f"goal_date_{laptop}": "2026-05-01"}), laptop)

    before = row_for(get_budget_page(2026, 3), "Laptop")["presets"]["goal"]
    set_budget(conn, laptop, "2026-03-01", 30000)
    after = row_for(get_budget_page(2026, 3), "Laptop")["presets"]["goal"]

    assert before == after == 300.0


def test_spending_before_the_first_budget_shows_as_over(conn, category, account_factory, transaction_factory):
    groceries = category("Test Groceries")
    checking = account_factory("Test Checking")
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id) VALUES ('contains', 'MARKET', ?)",
        (groceries,),
    )
    transaction_factory(checking, "2026-01-10", -30000, "MARKET")
    set_budget(conn, groceries, "2026-03-01", 10000)
    rebuild_ledger()

    row = row_for(get_budget_page(2026, 1), "Test Groceries")

    assert row["rollover"] == -300.0
    assert row["status_text"] == "Over by 300.00"


def test_the_spending_average_only_counts_months_with_data(
    conn, category, account_factory, transaction_factory
):
    groceries = category("Test Groceries")
    checking = account_factory("Test Checking")
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id) VALUES ('contains', 'MARKET', ?)",
        (groceries,),
    )
    transaction_factory(checking, "2026-02-10", -30000, "MARKET")
    transaction_factory(checking, "2026-02-12", 5000, "MARKET REFUND")
    rebuild_ledger()

    assert row_for(get_budget_page(2026, 3), "Test Groceries")["presets"]["avg3"] == 250.0


def test_goal_without_a_date_suggests_the_whole_remainder(conn, category):
    laptop = category("Laptop")
    save_goal(FakeForm({f"goal_amount_{laptop}": "500"}), laptop)

    page = get_budget_page(2026, 3)

    assert row_for(page, "Laptop")["presets"]["goal"] == 500.0


def test_deleting_a_goal_drops_its_preset(conn, category):
    laptop = category("Laptop")
    save_goal(FakeForm({f"goal_amount_{laptop}": "500"}), laptop)
    delete_goal(laptop)

    page = get_budget_page(2026, 3)
    row = row_for(page, "Laptop")

    assert row["goal"] is None
    assert "goal" not in row["presets"]


def test_move_budget_transfers_between_categories(conn, category):
    source = category("Test Groceries")
    destination = category("Dining")
    set_budget(conn, source, "2026-03-01", 50000)

    moved = move_budget(
        FakeForm({f"move_to_{source}": str(destination), f"move_amount_{source}": "100"}),
        source,
        "2026-03-01",
    )

    page = get_budget_page(2026, 3)

    assert moved == 100.0
    assert row_for(page, "Test Groceries")["by_period"]["2026-03-01"]["assigned"] == 400.0
    assert row_for(page, "Dining")["by_period"]["2026-03-01"]["assigned"] == 100.0


def test_moving_more_than_is_assigned_is_refused(conn, category):
    source = category("Test Groceries")
    destination = category("Dining")
    set_budget(conn, source, "2026-03-01", 10000)

    with pytest.raises(ValueError, match=r"Only 100\.00 is assigned"):
        move_budget(
            FakeForm({f"move_to_{source}": str(destination), f"move_amount_{source}": "500"}),
            source,
            "2026-03-01",
        )

    page = get_budget_page(2026, 3)

    assert row_for(page, "Test Groceries")["by_period"]["2026-03-01"]["assigned"] == 100.0
    assert row_for(page, "Dining")["by_period"]["2026-03-01"]["assigned"] == 0.0


def test_move_budget_rejects_a_self_transfer(conn, category):
    source = category("Test Groceries")
    set_budget(conn, source, "2026-03-01", 10000)

    moved = move_budget(
        FakeForm({f"move_to_{source}": str(source), f"move_amount_{source}": "50"}),
        source,
        "2026-03-01",
    )

    assert moved == 0.0


def test_carry_in_crosses_a_year_boundary(conn, category, account_factory, transaction_factory):
    groceries = category("Test Groceries")
    checking = account_factory("Test Checking")
    set_budget(conn, groceries, "2025-12-01", 10000)
    set_budget(conn, groceries, "2026-01-01", 10000)
    conn.execute(
        "INSERT INTO account_rules (match_type, pattern, account_id) VALUES ('contains', 'COFFEE', ?)",
        (groceries,),
    )
    transaction_factory(checking, "2025-12-30", -3000, "COFFEE")
    rebuild_ledger()

    row = row_for(get_budget_page(2026, 1), "Test Groceries")

    assert row["carry_in"] == 70.0
    assert row["rollover"] == 170.0


def test_move_budget_only_moves_into_a_budget_category(conn, category, account_factory):
    source = category("Test Groceries")
    checking = account_factory("Test Checking")
    set_budget(conn, source, "2026-03-01", 10000)

    with pytest.raises(ValueError):
        move_budget(
            FakeForm({f"move_to_{source}": checking, f"move_amount_{source}": "50"}), source, "2026-03-01"
        )


@pytest.mark.parametrize("amount", ["-50", "0", "abc", "nan"])
def test_move_budget_rejects_an_amount_that_is_not_positive(conn, category, amount):
    source = category("Test Groceries")
    destination = category("Test Dining")
    set_budget(conn, source, "2026-03-01", 10000)

    with pytest.raises(ValueError):
        move_budget(
            FakeForm({f"move_to_{source}": destination, f"move_amount_{source}": amount}),
            source,
            "2026-03-01",
        )


def test_a_goal_date_must_be_a_date(conn, category):
    rent = category("Rent")

    with pytest.raises(ValueError):
        save_goal(FakeForm({f"goal_amount_{rent}": "2000", f"goal_date_{rent}": "soon"}), rent)


def test_status_is_green_when_nothing_is_spent(conn, category):
    groceries = category("Test Groceries")
    set_budget(conn, groceries, "2026-03-01", 50000)

    row = row_for(get_budget_page(2026, 3), "Test Groceries")

    assert row["status_color"] == "green"
    assert row["status_text"] == "On track"


def test_pace_is_absent_outside_the_current_month(conn, category):
    groceries = category("Test Groceries")
    set_budget(conn, groceries, "2026-03-01", 50000)

    assert row_for(get_budget_page(2026, 3), "Test Groceries")["pace"] is None


@pytest.fixture
def client(conn, category):
    from intent_ledger import create_app

    parent = conn.execute("INSERT INTO accounts (name, type) VALUES ('Living', 'expense')").lastrowid
    conn.commit()

    rent = category("Rent", parent_account_id=parent)
    set_budget(conn, rent, "2026-03-01", 120000)
    save_goal(FakeForm({f"goal_amount_{rent}": "2000", f"goal_date_{rent}": "2026-06-01"}), rent)

    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    with app.test_client() as test_client:
        with test_client.session_transaction() as session:
            session["finance_year"] = 2026
            session["finance_month"] = 3
        yield test_client


def test_budget_page_renders(client):
    response = client.get("/budget")

    assert response.status_code == 200
    assert b"data-budget" in response.data
    assert b"data-rail" in response.data


def test_save_action_persists_assigned_amounts(client, conn, category):
    dining = category("Test Dining")

    response = client.post("/budget", data={"action": "save", f"budget_{dining}": "42.50"})

    assert response.status_code == 302
    row = conn.execute(
        "SELECT amount_cents FROM budgets WHERE account_id = ? AND period = '2026-03-01'",
        (dining,),
    ).fetchone()
    assert row["amount_cents"] == 4250


def test_saving_leaves_categories_missing_from_the_form_alone(client, conn, category):
    dining = category("Test Dining")
    rent = category("Test Rent")
    set_budget(conn, rent, "2026-03-01", 90000)

    client.post("/budget", data={"action": "save", f"budget_{dining}": "42.50"})

    row = conn.execute(
        "SELECT amount_cents FROM budgets WHERE account_id = ? AND period = '2026-03-01'", (rent,)
    ).fetchone()
    assert row["amount_cents"] == 90000


def test_move_budget_action_moves_money(client, conn, category):
    source = category("Test Groceries")
    destination = category("Test Dining")
    set_budget(conn, source, "2026-03-01", 30000)

    client.post(
        "/budget",
        data={
            "action": f"move_budget:{source}",
            f"move_to_{source}": str(destination),
            f"move_amount_{source}": "75",
        },
    )

    amounts = {
        r["account_id"]: r["amount_cents"]
        for r in conn.execute(
            "SELECT account_id, amount_cents FROM budgets WHERE period = '2026-03-01'"
        ).fetchall()
    }

    assert amounts[source] == 22500
    assert amounts[destination] == 7500


def test_goal_actions_save_and_delete(client, conn, category):
    laptop = category("Laptop")

    client.post(
        "/budget",
        data={
            "action": f"save_goal:{laptop}",
            f"goal_amount_{laptop}": "900",
        },
    )
    assert (
        conn.execute("SELECT goal_target_cents FROM accounts WHERE id = ?", (laptop,)).fetchone()[
            "goal_target_cents"
        ]
        == 90000
    )

    client.post("/budget", data={"action": f"delete_goal:{laptop}"})
    assert (
        conn.execute("SELECT goal_target_cents FROM accounts WHERE id = ?", (laptop,)).fetchone()[
            "goal_target_cents"
        ]
        is None
    )


@pytest.mark.parametrize("value", ["1,000", "abc", "nan", "-5"])
def test_a_malformed_budget_is_rejected_not_saved_as_zero(client, conn, category, value):
    dining = category("Test Dining")
    set_budget(conn, dining, "2026-03-01", 5000)

    response = client.post("/budget", data={"action": "save", f"budget_{dining}": value})

    assert response.status_code == 302
    row = conn.execute(
        "SELECT amount_cents FROM budgets WHERE account_id = ? AND period = '2026-03-01'", (dining,)
    ).fetchone()
    assert row["amount_cents"] == 5000


def test_a_malformed_budget_action_is_a_message_not_a_crash(client):
    assert client.post("/budget", data={"action": "delete_goal:abc"}).status_code == 302
