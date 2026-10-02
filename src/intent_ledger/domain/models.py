from dataclasses import dataclass


class _FieldAccessible:
    """Make a dataclass support row["field"] bracket access."""

    __slots__ = ()

    def __getitem__(self, key: str):
        return getattr(self, key)


@dataclass(frozen=True, slots=True)
class Account(_FieldAccessible):
    id: int
    name: str
    type: str
    budget: bool
    is_active: bool
    needs_review: bool
    role: str | None = None
    parent_account_id: int | None = None
    institution: str | None = None
    account_number_last4: str | None = None

    @classmethod
    def from_row(cls, row) -> "Account":
        return cls(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            budget=bool(row["budget"]),
            is_active=bool(row["is_active"]),
            needs_review=bool(row["needs_review"]),
            role=row["role"],
            parent_account_id=row["parent_account_id"],
            institution=row["institution"],
            account_number_last4=row["account_number_last4"],
        )


@dataclass(frozen=True, slots=True)
class Payee(_FieldAccessible):
    id: int
    name: str
    account_id: int | None
    is_system: bool = False

    @classmethod
    def from_row(cls, row) -> "Payee":
        return cls(
            id=row["id"],
            name=row["canonical_name"],
            account_id=row["account_id"],
            is_system=bool(row["is_system"]),
        )


@dataclass(frozen=True, slots=True)
class Counterparty(_FieldAccessible):
    id: int
    name: str

    @classmethod
    def from_row(cls, row) -> "Counterparty":
        return cls(id=row["id"], name=row["name"])
