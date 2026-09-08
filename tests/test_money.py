from intent_ledger.domain.money import MIRRORED_TYPES, Money


def test_amount_converts_cents_to_dollars():
    assert Money(12345).amount == 123.45
    assert Money(-500).amount == -5.0


def test_from_dollars_rounds_to_the_nearest_cent():
    assert Money.from_dollars(19.999) == Money(2000)
    assert Money.from_dollars(None) == Money(0)


def test_signed_for_display_flips_expense_and_income_only():
    assert Money(500).signed_for_display("expense") == Money(-500)
    assert Money(500).signed_for_display("income") == Money(-500)
    assert Money(500).signed_for_display("asset") == Money(500)
    assert Money(500).signed_for_display("liability") == Money(500)
    assert {"expense", "income"} == MIRRORED_TYPES


def test_arithmetic():
    assert Money(300) + Money(200) == Money(500)
    assert Money(300) - Money(200) == Money(100)
    assert -Money(300) == Money(-300)


def test_ordering_compares_by_cents():
    assert Money(100) < Money(200)
    assert Money(200) > Money(100)
    assert sorted([Money(300), Money(-100), Money(0)]) == [Money(-100), Money(0), Money(300)]


def test_str_formats_with_thousands_separator_and_two_decimals():
    assert str(Money(123456789)) == "1,234,567.89"
    assert str(Money(2000)) == "20.00"
    assert str(Money(-500)) == "-5.00"


def test_format_with_no_spec_matches_str():
    money = Money(123456789)
    assert f"{money}" == str(money) == "1,234,567.89"


def test_format_with_explicit_spec_uses_the_raw_amount():
    assert f"{Money(2050):.1f}" == "20.5"
