import calendar
from collections import defaultdict
from datetime import date, timedelta

from intent_ledger.accounting.repositories.subscriptions import SubscriptionRepository
from intent_ledger.analytics.reports import get_recurring_payments
from intent_ledger.db import db
from intent_ledger.domain.money import Money
from intent_ledger.settings import today

CADENCES = ("weekly", "monthly", "quarterly", "yearly")

_CADENCE_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}
_GRACE_DAYS = {"weekly": 3, "monthly": 5, "quarterly": 10, "yearly": 15}


def get_subscriptions_page():
    with db.transaction() as conn:
        rows = conn.execute("""
            SELECT
                s.id, s.payee_id, s.account_id, s.name, s.amount_cents,
                s.cadence, s.first_seen_date, s.status, s.cancelled_at, s.notes,
                m.canonical_name AS payee_name
            FROM subscriptions s
            JOIN payees m ON m.id = s.payee_id
            ORDER BY s.status, s.name, m.canonical_name
        """).fetchall()

        charge_rows = conn.execute("""
            SELECT
                subscription_id,
                MAX(posted_date) AS last_date,
                SUM(amount_cents) AS lifetime,
                COUNT(*) AS n
            FROM subscriptions_charges
            GROUP BY subscription_id
        """).fetchall()

    charges_by_sub = {r["subscription_id"]: r for r in charge_rows}
    tracked_payee_ids = {r["payee_id"] for r in rows}

    today_ = today()
    active, cancelled = [], []

    for r in rows:
        charge = charges_by_sub.get(r["id"])
        anchor = (
            date.fromisoformat(charge["last_date"]) if charge else date.fromisoformat(r["first_seen_date"])
        )
        next_expected = _advance(anchor, r["cadence"])
        missed = r["status"] == "active" and today_ > next_expected + timedelta(
            days=_GRACE_DAYS[r["cadence"]]
        )

        item = {
            "id": r["id"],
            "name": r["name"] or r["payee_name"],
            "payee": r["payee_name"],
            "payee_id": r["payee_id"],
            "account_id": r["account_id"],
            "amount": Money(r["amount_cents"]).amount,
            "cadence": r["cadence"],
            "status": r["status"],
            "first_seen_date": r["first_seen_date"],
            "cancelled_at": r["cancelled_at"],
            "next_expected_date_raw": next_expected,
            "next_expected_date": next_expected.strftime("%b %d"),
            "missed": missed,
            "lifetime_paid": Money(charge["lifetime"] if charge else 0).amount,
            "charge_count": charge["n"] if charge else 0,
            "notes": r["notes"],
        }
        (active if r["status"] == "active" else cancelled).append(item)

    candidates_by_payee = {
        c["payee_id"]: {
            "payee_id": c["payee_id"],
            "payee": c["payee"],
            "amount": c["amount"],
            "cadence": c["frequency"].lower(),
            "occurrences": c["occurrences"],
            "last_charged": c["last_payment"].strftime("%b %d"),
        }
        for c in get_recurring_payments()
        if c["payee_id"] not in tracked_payee_ids and c["next_payment"] != "Canceled"
    }

    try:
        with db.transaction() as conn:
            for c in _account_tagged_candidates(conn, tracked_payee_ids):
                candidates_by_payee.setdefault(c["payee_id"], c)
    except ValueError:
        pass  # no 'Subscriptions' category account configured yet

    candidates = list(candidates_by_payee.values())

    monthly_total = sum(_monthly_equivalent(s["amount"], s["cadence"]) for s in active)

    due_soon = [
        s
        for s in active
        if not s["missed"] and today_ <= s["next_expected_date_raw"] <= today_ + timedelta(days=7)
    ]

    for s in active:
        del s["next_expected_date_raw"]

    return {
        "active": active,
        "cancelled": cancelled,
        "candidates": candidates,
        "totals": {
            "monthly": round(monthly_total, 2),
            "annualized": round(monthly_total * 12, 2),
            "missed_count": sum(1 for s in active if s["missed"]),
            "due_7d_amount": round(sum(s["amount"] for s in due_soon), 2),
            "due_7d_count": len(due_soon),
        },
    }


def _advance(d: date, cadence: str) -> date:
    if cadence == "weekly":
        return d + timedelta(days=7)

    months = _CADENCE_MONTHS[cadence]
    total = d.year * 12 + (d.month - 1) + months
    year, month = total // 12, total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _monthly_equivalent(amount, cadence):
    if cadence == "weekly":
        return amount * 52 / 12
    if cadence == "quarterly":
        return amount / 3
    if cadence == "yearly":
        return amount / 12
    return amount


def _account_tagged_candidates(conn, tracked_payee_ids):
    """Payees with transactions already posted to the Subscriptions
    category account, even if too few/irregular to be auto-detected as
    recurring - categorizing an expense there is itself a strong signal.
    """
    account_id = _subscription_account_id(conn)

    rows = conn.execute(
        """
        SELECT t.payee_id, m.canonical_name AS payee_name,
               t.posted_date, l.amount_cents
        FROM ledger l
        JOIN transactions t ON t.id = l.transaction_id
        JOIN payees m ON m.id = t.payee_id
        WHERE l.account_id = ?
        ORDER BY t.payee_id, t.posted_date
    """,
        (account_id,),
    ).fetchall()

    by_payee = defaultdict(list)
    for r in rows:
        by_payee[r["payee_id"]].append(r)

    candidates = []
    for payee_id, txns in by_payee.items():
        if payee_id in tracked_payee_ids:
            continue

        last = txns[-1]
        candidates.append(
            {
                "payee_id": payee_id,
                "payee": last["payee_name"],
                "amount": Money(-last["amount_cents"]).amount,
                "cadence": _guess_cadence(txns),
                "occurrences": len(txns),
                "last_charged": date.fromisoformat(last["posted_date"]).strftime("%b %d"),
            }
        )

    return candidates


def _guess_cadence(txns) -> str:
    if len(txns) < 2:
        return "monthly"

    dates = [date.fromisoformat(t["posted_date"]) for t in txns]
    intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
    avg_interval = sum(intervals) / len(intervals)

    if avg_interval <= 10:
        return "weekly"
    if avg_interval <= 100:
        return "monthly"
    if avg_interval <= 200:
        return "quarterly"
    return "yearly"


def create_subscription(form) -> int:
    fields = _parse_form(form)
    fields["first_seen_date"] = _derive_first_seen_date(fields["payee_id"], fields["amount_cents"])

    with db.transaction() as conn:
        fields["account_id"] = _subscription_account_id(conn)
        subscription_id = SubscriptionRepository(conn).create(fields)

    rebuild_subscription_matches()
    return subscription_id


def update_subscription(subscription_id: int, form):
    fields = _parse_form(form)
    fields["id"] = subscription_id

    with db.transaction() as conn:
        repo = SubscriptionRepository(conn)

        if repo.get(subscription_id) is None:
            raise ValueError("Subscription not found.")

        fields["account_id"] = _subscription_account_id(conn)
        repo.update(fields)

    rebuild_subscription_matches()


def cancel_subscription(subscription_id: int):
    with db.transaction() as conn:
        SubscriptionRepository(conn).cancel(subscription_id)

    rebuild_subscription_matches()


def reactivate_subscription(subscription_id: int):
    with db.transaction() as conn:
        SubscriptionRepository(conn).reactivate(subscription_id)

    rebuild_subscription_matches()


def delete_subscription(subscription_id: int):
    with db.transaction() as conn:
        repo = SubscriptionRepository(conn)

        if repo.charge_count(subscription_id):
            raise ValueError("Cancel this subscription instead, it already has matched charges.")

        repo.delete(subscription_id)


def rebuild_subscription_matches():
    with db.transaction() as conn:
        conn.execute("DELETE FROM subscriptions_charges")

        subs = conn.execute("SELECT * FROM subscriptions").fetchall()
        for sub in subs:
            rows = conn.execute(
                """
                SELECT t.id AS transaction_id, t.posted_date, l.amount_cents
                FROM ledger l
                JOIN transactions t ON t.id = l.transaction_id
                JOIN accounts a ON a.id = l.account_id
                WHERE a.type = 'expense'
                  AND l.amount_cents = ?
                  AND t.payee_id = ?
                  AND (? IS NULL OR t.posted_date <= ?)
            """,
                (
                    -sub["amount_cents"],
                    sub["payee_id"],
                    sub["cancelled_at"],
                    sub["cancelled_at"],
                ),
            ).fetchall()

            for r in rows:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO subscriptions_charges (
                        subscription_id, transaction_id, amount_cents, posted_date
                    )
                    VALUES (?, ?, ?, ?)
                """,
                    (sub["id"], r["transaction_id"], -r["amount_cents"], r["posted_date"]),
                )


def _subscription_account_id(conn) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE type = 'expense' AND name = 'Subscriptions'").fetchone()
    if not row:
        raise ValueError("'Subscriptions' category account not found.")
    return row["id"]


def _parse_form(form):
    payee_id = form.get("payee_id", type=int)
    if not payee_id:
        raise ValueError("Payee is required.")

    cadence = form.get("cadence", "").strip()
    if cadence not in CADENCES:
        raise ValueError("Choose a valid cadence.")

    amount = form.get("amount", type=float)
    if not amount or amount <= 0:
        raise ValueError("Amount must be a positive number.")

    return {
        "payee_id": payee_id,
        "name": form.get("name", "").strip() or None,
        "amount_cents": -round(amount * 100),
        "cadence": cadence,
        "notes": form.get("notes", "").strip() or None,
    }


def _derive_first_seen_date(payee_id: int, amount_cents: int) -> str:
    with db.transaction() as conn:
        row = conn.execute(
            """
            SELECT MIN(t.posted_date) AS d
            FROM ledger l
            JOIN transactions t ON t.id = l.transaction_id
            JOIN accounts a ON a.id = l.account_id
            WHERE a.type = 'expense' AND l.amount_cents = ? AND t.payee_id = ?
        """,
            (-amount_cents, payee_id),
        ).fetchone()

    return row["d"] or today().isoformat()
