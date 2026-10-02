import re
from datetime import date

from intent_ledger.domain.money import Money

_INTEGER = re.compile(r"-?[0-9]+")


def text(values, name: str) -> str:
    return values.get(name, "").strip()


def whole_number(raw: str, label: str) -> int:
    if not _INTEGER.fullmatch(raw.strip()):
        raise ValueError(f"Choose a valid {label}.")
    return int(raw)


def integer(values, name: str, label: str) -> int:
    return whole_number(text(values, name), label)


def optional_integer(values, name: str, label: str) -> int | None:
    return integer(values, name, label) if text(values, name) else None


def integers(values, name: str, label: str) -> list[int]:
    return [whole_number(raw, label) for raw in values.getlist(name)]


def money(values, name: str, label: str) -> Money:
    try:
        return Money.parse(text(values, name))
    except ValueError:
        raise ValueError(f"{label} must be an amount like 1234.50.") from None


def positive_money(values, name: str, label: str) -> Money:
    amount = money(values, name, label)
    if amount.cents <= 0:
        raise ValueError(f"{label} must be more than zero.")
    return amount


def optional_money(values, name: str, label: str) -> Money | None:
    return money(values, name, label) if text(values, name) else None


def iso_date(values, name: str, label: str) -> str:
    try:
        return date.fromisoformat(text(values, name)).isoformat()
    except ValueError:
        raise ValueError(f"{label} must be a date like 2026-01-31.") from None


def optional_iso_date(values, name: str, label: str) -> str | None:
    return iso_date(values, name, label) if text(values, name) else None


def choice(values, name: str, choices, label: str) -> str:
    value = text(values, name)
    if value not in choices:
        raise ValueError(f"Choose a valid {label}.")
    return value


def json_object(body) -> dict:
    if not isinstance(body, dict):
        raise ValueError("The request body must be a JSON object.")
    return body
