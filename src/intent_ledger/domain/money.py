from dataclasses import dataclass

MIRRORED_TYPES = frozenset({"expense", "income"})


@dataclass(frozen=True, order=True, slots=True)
class Money:
    """Integer-cents money value type.

    Handles cents-to-dollars conversion, rounding, and display-sign
    normalization for expense and income accounts.
    """

    cents: int

    @classmethod
    def from_dollars(cls, amount: float | None) -> "Money":
        return cls(round((amount or 0) * 100))

    @property
    def amount(self) -> float:
        return self.cents / 100.0

    def signed_for_display(self, account_type: str) -> "Money":
        """Expense and income accounts use the opposite sign convention on the
        ledger: negative means money in, while positive means spent or earned.
        """
        return Money(-self.cents) if account_type in MIRRORED_TYPES else self

    def __add__(self, other: "Money") -> "Money":
        return Money(self.cents + other.cents)

    def __sub__(self, other: "Money") -> "Money":
        return Money(self.cents - other.cents)

    def __neg__(self) -> "Money":
        return Money(-self.cents)

    def __format__(self, spec: str) -> str:
        return str(self) if not spec else format(self.amount, spec)

    def __str__(self) -> str:
        return f"{self.amount:,.2f}"
