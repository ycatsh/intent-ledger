from datetime import date
from pathlib import Path
from typing import Protocol, TypedDict


class ParsedTransaction(TypedDict):
    posted_date: date
    raw_description: str
    amount_cents: int
    balance_cents: int | None


class ParsedStatement(TypedDict):
    transactions: list[ParsedTransaction]
    row_errors: list[str]


class StatementParser(Protocol):
    slug: str
    display_name: str
    file_extensions: tuple[str, ...]

    def sniff(self, path: Path) -> bool: ...

    def parse(self, path: Path) -> ParsedStatement: ...
