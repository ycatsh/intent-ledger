import hashlib
import re
import unicodedata
import uuid
from collections import Counter

_TRAILING_STATE_RE = re.compile(r"\s+[A-Z]{2}$")
_FIELD_SPLIT_RE = re.compile(r"[/\s@]+")
_DIGITS_RE = re.compile(r"\d+")
_REFERENCE_FIELD_LEN = 12


def normalize_description(raw: str) -> dict:
    cleaned = _clean_text(raw)

    return {
        "payee": _fuzzy_group_key(cleaned),
        "note": "",
        "structured": cleaned,
    }


def extract_payee_key(text: str) -> str:
    return _fuzzy_group_key(_clean_text(text or ""))


def _clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"\s+", " ", text)

    return text.strip().upper()


def _fuzzy_group_key(text: str) -> str:
    text = _TRAILING_STATE_RE.sub("", text or "")
    raw_fields = [f for f in _FIELD_SPLIT_RE.split(text) if f]
    repeated = {token for token, count in Counter(f.upper() for f in raw_fields).items() if count > 1}
    fields = []
    for raw_field in raw_fields:
        if raw_field.upper() in repeated:
            continue
        cleaned = _clean_field(raw_field)
        if cleaned:
            fields.append(cleaned)
    return " ".join(fields[:3])


def _clean_field(field: str) -> str:
    if field.isdigit() or len(field) >= _REFERENCE_FIELD_LEN:
        return ""
    letters = _DIGITS_RE.sub("", field)
    return letters if len(letters) >= 3 else ""


def fingerprint(
    account_id: int,
    posted_date: str,
    amount_cents: int,
    balance_cents: int | None,
    description: str,
) -> str:
    payload = "|".join(
        [
            str(account_id),
            posted_date,
            str(amount_cents),
            str(balance_cents),
            description,
        ]
    )

    return hashlib.sha256(payload.encode()).hexdigest()


def manual_fingerprint() -> str:
    return hashlib.sha256(f"manual:{uuid.uuid4().hex}".encode()).hexdigest()
