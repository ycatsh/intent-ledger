import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from intent_ledger.accounting.repositories.accounts import AccountRepository
from intent_ledger.accounting.repositories.payees import PayeeRepository
from intent_ledger.db import db
from intent_ledger.domain.models import Account, Payee
from intent_ledger.domain.money import MIRRORED_TYPES, Money
from intent_ledger.settings import today, today_in


def get_active_accounts():
    return _rows(
        "WHERE a.is_active = 1 AND (a.type = 'asset' or a.type = 'equity' or a.type = 'liability')", []
    )


def get_all_accounts() -> list[Account]:
    with db.transaction() as conn:
        return AccountRepository(conn).active_ordered_by_type()


def get_accounts_needing_review_count() -> int:
    with db.transaction() as conn:
        return AccountRepository(conn).needs_review_count()


def create_account(name: str, account_type: str) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("Name is required.")
    if account_type not in ("asset", "liability"):
        raise ValueError("Account type must be asset or liability.")

    with db.transaction() as conn:
        return AccountRepository(conn).create(name, account_type)


def get_or_create_expense_account(conn, name: str) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("Category is required.")

    return AccountRepository(conn).get_or_create_expense(name)


def get_account(account_id: int):
    rows = _rows("WHERE a.id = ?", [account_id])
    return rows[0] if rows else None


def _rows(where="", params=()):
    with db.transaction() as conn:
        return conn.execute(
            f"""
            SELECT
                a.id,
                a.institution,
                a.name,
                a.account_number_last4 as last4,
                a.type,
                a.budget,
                COALESCE(SUM(l.amount_cents), 0) / 100.0 balance,
                COUNT(l.id) transactions
            FROM accounts a
            LEFT JOIN ledger l
                ON l.account_id = a.id
            LEFT JOIN transactions t
                ON t.id = l.transaction_id
            {where}
            GROUP BY a.id
            ORDER BY a.id ASC
            """,
            params,
        ).fetchall()


def get_all_payees() -> list[Payee]:
    with db.transaction() as conn:
        return PayeeRepository(conn).list_all_ordered()


def get_all_account_names() -> list[str]:
    with db.transaction() as conn:
        return AccountRepository(conn).all_names()


def get_payee_default_categories() -> dict[str, str]:
    with db.transaction() as conn:
        return PayeeRepository(conn).default_account_names()


# Ledger Search:

SQL_OPS = {":": "=", "=": "=", ">": ">", "<": "<", ">=": ">=", "<=": "<="}

_TOKEN = re.compile(
    r"^(payee|category|date|amount|amt)(:|=|>=|<=|>|<)(.+)$",
    re.I,
)

OPS = {">", "<", ">=", "<=", ":", "="}

_FIELD_ALIASES = {"amt": "amount"}


@dataclass(frozen=True)
class Filter:
    field: str
    op: str
    value: object


def parse_search(search: str | None) -> tuple[list[str], list[Filter]]:
    """Split free text from field:value tokens (payee, category, date, amount/amt)."""
    text: list[str] = []
    filters: list[Filter] = []

    if not search:
        return text, filters

    for token in search.split():
        m = _TOKEN.fullmatch(token)
        if not m:
            text.append(token)
            continue

        field, op, value = m.groups()
        field = _FIELD_ALIASES.get(field.lower(), field.lower())

        if op not in OPS:
            continue

        if field == "amount":
            try:
                value = float(value)
            except ValueError:
                continue

        elif field == "date":
            try:
                value = datetime.strptime(value, "%d/%m/%Y").date().isoformat()
            except ValueError:
                continue

        filters.append(Filter(field, op, value))

    return text, filters


# Ledger queries:

_OTHER_LEG_COLUMNS = """
        (
            SELECT GROUP_CONCAT(DISTINCT ca2.name)
            FROM ledger cl2
            JOIN accounts ca2 ON ca2.id = cl2.account_id
            WHERE cl2.group_id = l.group_id AND cl2.account_id != l.account_id
        ) AS category,
        (
            SELECT ca2.id
            FROM ledger cl2
            JOIN accounts ca2 ON ca2.id = cl2.account_id
            WHERE cl2.group_id = l.group_id AND cl2.account_id != l.account_id
            LIMIT 1
        ) AS category_account_id,
        (
            SELECT ca2.type
            FROM ledger cl2
            JOIN accounts ca2 ON ca2.id = cl2.account_id
            WHERE cl2.group_id = l.group_id AND cl2.account_id != l.account_id
            LIMIT 1
        ) AS category_type,"""


def _load_splits(conn, transaction_hashes, exclude_account_id=None):
    if not transaction_hashes:
        return {}

    placeholders = ",".join("?" * len(transaction_hashes))

    if exclude_account_id is not None:
        exclude_clause = "cl.account_id != ?"
        params = [*transaction_hashes, exclude_account_id]
    else:
        exclude_clause = "cl.account_id != t2.account_id"
        params = list(transaction_hashes)

    rows = conn.execute(
        f"""
        SELECT
            t2.transaction_hash AS transaction_hash,
            ca2.id AS account_id,
            ca2.name AS account,
            ca2.type AS type,
            cl.description AS note,
            -cl.amount_cents AS amount_cents
        FROM ledger cl
        JOIN transactions t2 ON t2.id = cl.transaction_id
        JOIN accounts ca2 ON ca2.id = cl.account_id
        WHERE t2.transaction_hash IN ({placeholders}) AND {exclude_clause}
        ORDER BY cl.id
    """,
        params,
    ).fetchall()

    by_hash = defaultdict(list)
    for r in rows:
        by_hash[r["transaction_hash"]].append(
            {
                "account_id": r["account_id"],
                "account": r["account"],
                "amount": Money(r["amount_cents"]).amount,
                "type": r["type"],
                "note": r["note"],
            }
        )

    return by_hash


def _attach_splits(conn, rows, exclude_account_id=None):
    hashes = [r["transaction_hash"] for r in rows if r["has_split"]]
    by_hash = _load_splits(conn, hashes, exclude_account_id=exclude_account_id)

    for row in rows:
        row["splits"] = by_hash.get(row["transaction_hash"], [])

    return rows


def get_ledger_entries(account_id: int | None, search=None, sort="date_desc", limit=None):
    text, filters = parse_search(search)

    sql = f"""
    SELECT
        t.posted_date AS date,
        t.transaction_hash AS transaction_hash,
        t.status AS status,
        {_OTHER_LEG_COLUMNS}
        m.id AS payee_id,
        COALESCE(m.canonical_name, t.raw_description) AS payee,
        l.amount_cents / 100.0 AS amount,
        t.note AS note,
        ov.id AS override_id,
        EXISTS (
            SELECT 1 FROM transactions_splits ts
            WHERE ts.transaction_hash = t.transaction_hash
        ) AS has_split,
        GROUP_CONCAT(DISTINCT p.name) AS projects,
        GROUP_CONCAT(DISTINCT p.id) AS project_ids,
        cp.id AS counterparty_id,
        cp.name AS counterparty
    FROM ledger l
    JOIN transactions t
      ON t.id = l.transaction_id

    LEFT JOIN payees m
      ON m.id = t.payee_id

    LEFT JOIN transactions_overrides ov
      ON ov.transaction_hash = t.transaction_hash

    LEFT JOIN transactions_projects tp
      ON tp.transaction_hash = t.transaction_hash

    LEFT JOIN projects p
      ON p.id = tp.project_id

    LEFT JOIN counterparties cp
      ON cp.id = t.counterparty_id

    WHERE l.account_id = ?
    """
    params = [account_id]

    if text:
        sql += """
        AND (
            EXISTS (
                SELECT 1 FROM ledger cl2
                JOIN accounts ca2 ON ca2.id = cl2.account_id
                WHERE cl2.group_id = l.group_id AND cl2.account_id != l.account_id
                AND LOWER(ca2.name) LIKE ?
            )
            OR LOWER(IFNULL(m.canonical_name,'')) LIKE ?
            OR LOWER(IFNULL(t.posted_date,'')) LIKE ?
            OR CAST(l.amount_cents/100.0 AS TEXT) LIKE ?
        )
        """
        q = f"%{search.lower()}%"
        params.extend([q, q, q, q])

    for f in filters:
        if f.field == "payee":
            sql += " AND LOWER(IFNULL(m.canonical_name,'')) LIKE ?"
            params.append(f"%{f.value.lower()}%")

        elif f.field == "category":
            sql += """
            AND EXISTS (
                SELECT 1 FROM ledger cl2
                JOIN accounts ca2 ON ca2.id = cl2.account_id
                WHERE cl2.group_id = l.group_id AND cl2.account_id != l.account_id
                AND LOWER(ca2.name) LIKE ?
            )
            """
            params.append(f"%{f.value.lower()}%")

        elif f.field == "amount":
            sql += f" AND ABS(l.amount_cents)/100.0 {SQL_OPS[f.op]} ?"
            params.append(f.value)

        elif f.field == "date":
            sql += f" AND t.posted_date {SQL_OPS[f.op]} ?"
            params.append(f.value)

    order_map = {
        "date_desc": "t.posted_date DESC, l.id DESC",
        "date_asc": "t.posted_date ASC, l.id ASC",
        "amount_desc": "l.amount_cents DESC",
        "amount_asc": "l.amount_cents ASC",
        "payee": "m.canonical_name ASC",
        "category": "category ASC",
    }

    sql += f"""
        GROUP BY l.id
        ORDER BY
            {order_map.get(sort, order_map["date_desc"])}
    """

    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    with db.transaction() as conn:
        rows = conn.execute(sql, params).fetchall()
        _attach_splits(conn, rows, exclude_account_id=account_id)

    return rows


def get_payee_transactions(payee_id: int, sort="date_desc"):
    sql = f"""
    SELECT
        t.posted_date AS date,
        t.transaction_hash AS transaction_hash,
        t.status AS status,
        ca.name AS account_name,
        ca.type AS account_type,
        ca.budget AS account_budget,
        {_OTHER_LEG_COLUMNS}
        m.id AS payee_id,
        COALESCE(m.canonical_name, t.raw_description) AS payee,
        t.amount_cents / 100.0 AS amount,
        t.note AS note,
        ov.id AS override_id,
        EXISTS (
            SELECT 1 FROM transactions_splits ts
            WHERE ts.transaction_hash = t.transaction_hash
        ) AS has_split,
        GROUP_CONCAT(DISTINCT p.name) AS projects,
        GROUP_CONCAT(DISTINCT p.id) AS project_ids
    FROM transactions t

    JOIN ledger l
      ON l.transaction_id = t.id AND l.account_id = t.account_id

    JOIN accounts ca
      ON ca.id = t.account_id

    LEFT JOIN payees m
      ON m.id = t.payee_id

    LEFT JOIN transactions_overrides ov
      ON ov.transaction_hash = t.transaction_hash

    LEFT JOIN transactions_projects tp
      ON tp.transaction_hash = t.transaction_hash

    LEFT JOIN projects p
      ON p.id = tp.project_id

    WHERE t.payee_id = ?

    GROUP BY t.id
    """

    order_map = {
        "date_desc": "t.posted_date DESC, t.id DESC",
        "date_asc": "t.posted_date ASC, t.id ASC",
        "amount_desc": "t.amount_cents DESC",
        "amount_asc": "t.amount_cents ASC",
        "category": "category ASC",
    }

    sql += f"""
        ORDER BY
            {order_map.get(sort, order_map["date_desc"])}
    """

    with db.transaction() as conn:
        rows = conn.execute(sql, [payee_id]).fetchall()
        _attach_splits(conn, rows, exclude_account_id=None)

    return rows


# Account/payee profile pages:


def get_account_view_page(account_id: int):
    with db.transaction() as conn:
        account_row = conn.execute(
            "SELECT id, name, type FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()

        if account_row is None:
            return None

        today_ = today_in(conn)
        twelve_months_ago = _start_of_month_shifted(today_, -12)

        stats = conn.execute(
            """
            SELECT
                COUNT(*) AS tx_count,
                MIN(t.posted_date) AS first_seen,
                MAX(t.posted_date) AS last_seen,
                COALESCE(SUM(l.amount_cents), 0) AS net_cents,
                COALESCE(SUM(CASE WHEN l.amount_cents > 0 THEN l.amount_cents END), 0) AS positive_cents,
                COALESCE(SUM(CASE WHEN l.amount_cents > 0 THEN 1 ELSE 0 END), 0) AS positive_count,
                COALESCE(SUM(CASE WHEN l.amount_cents < 0 THEN -l.amount_cents END), 0) AS negative_cents,
                COALESCE(SUM(CASE WHEN l.amount_cents < 0 THEN 1 ELSE 0 END), 0) AS negative_count,
                COALESCE(MIN(CASE WHEN l.amount_cents > 0 THEN l.amount_cents END), 0) AS typical_min_cents,
                COALESCE(MAX(CASE WHEN l.amount_cents > 0 THEN l.amount_cents END), 0) AS typical_max_cents
            FROM ledger l
            JOIN transactions t ON t.id = l.transaction_id
            WHERE l.account_id = ?
        """,
            (account_id,),
        ).fetchone()

        largest = conn.execute(
            """
            SELECT l.amount_cents AS amount_cents, t.posted_date AS posted_date
            FROM ledger l
            JOIN transactions t ON t.id = l.transaction_id
            WHERE l.account_id = ? AND l.amount_cents > 0
            ORDER BY l.amount_cents DESC
            LIMIT 1
        """,
            (account_id,),
        ).fetchone()

        monthly = conn.execute(
            """
            SELECT
                strftime('%Y-%m', t.posted_date) AS period,
                COALESCE(SUM(CASE WHEN l.amount_cents > 0 THEN l.amount_cents END), 0) AS positive_cents,
                COALESCE(SUM(CASE WHEN l.amount_cents < 0 THEN -l.amount_cents END), 0) AS negative_cents
            FROM ledger l
            JOIN transactions t ON t.id = l.transaction_id
            WHERE l.account_id = ?
              AND t.posted_date >= ?
            GROUP BY period
        """,
            (account_id, twelve_months_ago.isoformat()),
        ).fetchall()

        recent = get_ledger_entries(account_id, sort="date_desc", limit=25)

    first_seen = _parse_date(stats["first_seen"])
    last_seen = _parse_date(stats["last_seen"])
    months_active = _months_between(first_seen, today_) if first_seen else 1

    account_type = account_row["type"]
    sign = -1 if account_type in MIRRORED_TYPES else 1
    in_key, out_key = ("positive", "negative") if sign > 0 else ("negative", "positive")

    account = {
        "name": account_row["name"],
        "type": account_type,
        "tx_count": stats["tx_count"],
        "first_seen": stats["first_seen"] or "",
        "first_seen_short": _short_date(first_seen),
        "last_seen": stats["last_seen"] or "",
        "last_seen_short": _short_date(last_seen),
        "days_since": (today_ - last_seen).days if last_seen else 0,
        "net": Money(stats["net_cents"]).signed_for_display(account_type).amount,
        "in_total": Money(stats[f"{in_key}_cents"]).amount,
        "in_count": stats[f"{in_key}_count"],
        "out_total": Money(stats[f"{out_key}_cents"]).amount,
        "out_count": stats[f"{out_key}_count"],
        "avg_month": Money(stats["positive_cents"]).amount / min(max(months_active, 1), 12),
        "cadence": None,
        "typical_min": Money(stats["typical_min_cents"]).amount,
        "typical_max": Money(stats["typical_max_cents"]).amount,
        "largest": Money(largest["amount_cents"]).amount if largest else 0,
        "largest_date": largest["posted_date"] if largest else "",
    }

    monthly_by_period = {row["period"]: row for row in monthly}
    periods = _last_12_month_periods(today_)

    in_series = []
    out_series = []
    for period in periods:
        row = monthly_by_period.get(period)
        in_series.append(round(Money(row[f"{in_key}_cents"] if row else 0).amount, 2))
        out_series.append(round(Money(-(row[f"{out_key}_cents"] if row else 0)).amount, 2))

    charts = _monthly_flow_chart(periods, in_series, out_series)

    recent_transactions = [
        {
            "date": row["date"],
            "transaction_hash": row["transaction_hash"],
            "status": row["status"],
            "category": row["category"],
            "category_account_id": row["category_account_id"],
            "payee": row["payee"],
            "amount": row["amount"] * sign,
            "project": row["projects"],
            "has_split": row["has_split"],
            "splits": [{**s, "amount": s["amount"] * sign} for s in row["splits"]],
        }
        for row in recent
    ]

    return {
        "account": account,
        "account_id": account_id,
        "charts": charts,
        "recent_transactions": recent_transactions,
    }


def _payee_cash_flow(t):
    amount = t["amount"]
    is_suspense_liability = t["account_type"] == "liability" and t["account_budget"] == 0

    if is_suspense_liability:
        return (-amount, 0) if amount > 0 else (0, 0)
    if amount < 0:
        return (-amount, 0)
    if amount > 0:
        return (0, amount)
    return (0, 0)


def get_payee_view_page(payee_id: int):
    with db.transaction() as conn:
        payee_row = conn.execute(
            "SELECT id, canonical_name AS name FROM payees WHERE id = ?",
            (payee_id,),
        ).fetchone()

    if payee_row is None:
        return None

    transactions = get_payee_transactions(payee_id, sort="date_desc")

    today_ = today()
    dates = [_parse_date(t["date"]) for t in transactions]
    first_seen = min(dates) if dates else None
    last_seen = max(dates) if dates else None
    months_active = _months_between(first_seen, today_) if first_seen else 1

    flows = [(_payee_cash_flow(t), t) for t in transactions]
    paid = [p for (p, _), _t in flows if p]
    received = [r for (_, r), _t in flows if r]

    largest = max(paid) if paid else 0
    largest_date = next((t["date"] for (p, _), t in flows if p == largest), "") if paid else ""

    payee = {
        "name": payee_row["name"],
        "type": "payee",
        "tx_count": len(transactions),
        "first_seen": first_seen.isoformat() if first_seen else "",
        "first_seen_short": _short_date(first_seen),
        "last_seen": last_seen.isoformat() if last_seen else "",
        "last_seen_short": _short_date(last_seen),
        "days_since": (today_ - last_seen).days if last_seen else 0,
        "net": round(sum(received) - sum(paid), 2),
        "in_total": round(sum(received), 2),
        "in_count": len(received),
        "out_total": round(sum(paid), 2),
        "out_count": len(paid),
        "avg_month": round(sum(paid) / min(max(months_active, 1), 12), 2),
        "cadence": None,
        "typical_min": min(paid) if paid else 0,
        "typical_max": max(paid) if paid else 0,
        "largest": largest,
        "largest_date": largest_date,
    }

    periods = _last_12_month_periods(today_)
    paid_by_period = defaultdict(float)
    received_by_period = defaultdict(float)

    for t in transactions:
        period = t["date"][:7]
        p, r = _payee_cash_flow(t)
        if p:
            paid_by_period[period] += p
        if r:
            received_by_period[period] += r

    charts = _monthly_flow_chart(
        periods,
        [round(received_by_period.get(p, 0), 2) for p in periods],
        [round(-paid_by_period.get(p, 0), 2) for p in periods],
    )

    recent_transactions = [
        {
            "date": t["date"],
            "transaction_hash": t["transaction_hash"],
            "status": t["status"],
            "account_name": t["account_name"],
            "category": t["category"],
            "category_account_id": t["category_account_id"],
            "payee": t["payee"],
            "amount": t["amount"],
            "project": t["projects"],
            "has_split": t["has_split"],
            "splits": t["splits"],
        }
        for t in transactions[:25]
    ]

    return {
        "account": payee,
        "account_id": None,
        "charts": charts,
        "recent_transactions": recent_transactions,
    }


def _parse_date(value: str | None):
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def _short_date(d: date | None) -> str:
    return d.strftime("%b %d, %Y") if d else ""


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def _start_of_month_shifted(d: date, delta_months: int) -> date:
    total = d.year * 12 + (d.month - 1) + delta_months
    year, month = divmod(total, 12)
    return date(year, month + 1, 1)


def _monthly_flow_chart(periods: list[str], in_series: list[float], out_series: list[float]) -> dict:
    return {
        "monthly_flow": {
            "labels": periods,
            "datasets": [
                {"label": "In", "data": in_series, "color": "#3d9970"},
                {"label": "Out", "data": out_series, "color": "#c0392b"},
            ],
        },
    }


def _last_12_month_periods(today: date) -> list[str]:
    periods = []
    year, month = today.year, today.month

    for _ in range(12):
        periods.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1

    return list(reversed(periods))
