import math
import re
from dataclasses import dataclass
from fractions import Fraction

MIRRORED_TYPES = frozenset({"expense", "income"})

_AMOUNT = re.compile(r"(-?)([0-9]*)(?:\.([0-9]{1,2}))?")


@dataclass(frozen=True, order=True, slots=True)
class Money:
    """Integer-cents money value type."""

    cents: int

    @classmethod
    def parse(cls, text: str) -> "Money":
        match = _AMOUNT.fullmatch(text.strip())
        if match is None or not (match[2] or match[3]):
            raise ValueError(f"{text!r} is not an amount like 1234.50.")

        sign, whole, fraction = match.groups()
        cents = int(whole or 0) * 100 + int((fraction or "").ljust(2, "0"))
        return cls(-cents if sign else cents)

    @property
    def amount(self) -> float:
        return self.cents / 100.0

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


def scaled(cents: int, numerator: int, denominator: int) -> int:
    """Multiply cents by a fraction, rounding half a cent away from zero."""
    exact = Fraction(cents * numerator, denominator)
    whole = math.floor(abs(exact) + Fraction(1, 2))
    return whole if exact >= 0 else -whole
