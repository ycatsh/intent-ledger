import calendar
from collections import defaultdict
from datetime import date, timedelta

from intent_ledger import forms
from intent_ledger.accounting.repositories.payees import PayeeRepository
from intent_ledger.accounting.repositories.subscriptions import SubscriptionRepository
from intent_ledger.db import db
from intent_ledger.domain.money import Money, scaled
from intent_ledger.settings import today

CADENCES = ("weekly", "monthly", "quarterly", "yearly")

_CADENCE_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}
_PER_YEAR = {"weekly": 52, "monthly": 12, "quarterly": 4, "yearly": 1}
_GRACE_DAYS = {"weekly": 3, "monthly": 5, "quarterly": 10, "yearly": 15}
_CADENCE_RANGES = {"weekly": (5, 9), "monthly": (27, 32), "quarterly": (85, 97), "yearly": (350, 380)}


def classify_cadence(dates: list[date], max_variance_days=5) -> str | None:
    """Return the cadence of these dates, or None when the gaps are irregular or unknown."""
    dates = sorted(dates)
    if len(dates) < 2:
        return None

    intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
    if max(intervals) - min(intervals) > max_variance_days:
        return None

    avg_interval = round(sum(intervals) / len(intervals))
    return next((c for c, (lo, hi) in _CADENCE_RANGES.items() if lo <= avg_interval <= hi), None)


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

        monthly_cents = scaled(r["amount_cents"], _PER_YEAR[r["cadence"]], 12)
        item = {
            "id": r["id"],
            "name": r["name"] or r["payee_name"],
            "payee": r["payee_name"],
            "payee_id": r["payee_id"],
            "account_id": r["account_id"],
            "amount_cents": r["amount_cents"],
            "amount": Money(r["amount_cents"]).amount,
            "monthly_cents": monthly_cents,
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
            "cadence": c["cadence"],
            "occurrences": c["occurrences"],
            "last_charged": c["last_payment"].strftime("%b %d"),
        }
        for c in _detect_recurring_candidates()
        if c["payee_id"] not in tracked_payee_ids and c["next_payment"] != "Canceled"
    }

    try:
        with db.transaction() as conn:
            for c in _account_tagged_candidates(conn, tracked_payee_ids):
                candidates_by_payee.setdefault(c["payee_id"], c)
    except ValueError:
        pass

    candidates = list(candidates_by_payee.values())

    monthly_cents = sum(s["monthly_cents"] for s in active)
    yearly_cents = sum(s["amount_cents"] * _PER_YEAR[s["cadence"]] for s in active)

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
            "monthly": Money(monthly_cents).amount,
            "annualized": Money(yearly_cents).amount,
            "missed_count": sum(1 for s in active if s["missed"]),
            "due_7d_amount": Money(sum(s["amount_cents"] for s in due_soon)).amount,
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
                "cadence": classify_cadence([date.fromisoformat(t["posted_date"]) for t in txns])
                or "monthly",
                "occurrences": len(txns),
                "last_charged": date.fromisoformat(last["posted_date"]).strftime("%b %d"),
            }
        )

    return candidates


def _detect_recurring_candidates(min_occurrences=3):
    with db.transaction() as conn:
        rows = conn.execute("""
            SELECT
                p.posted_date,
                -p.amount_cents AS amount_cents,
                p.payee_id,
                m.canonical_name
            FROM income_expense_lines p
            JOIN payees m ON m.id = p.payee_id
            WHERE p.account_type = 'expense' AND p.amount_cents > 0
            ORDER BY p.payee_id, p.posted_date
        """).fetchall()

    groups = defaultdict(list)
    for row in rows:
        groups[(row["payee_id"], row["amount_cents"])].append(row)

    today_ = today()
    recurring = []

    for (_, amount), txns in groups.items():
        if len(txns) < min_occurrences:
            continue

        txns.sort(key=lambda x: x["posted_date"])
        dates = [date.fromisoformat(txn["posted_date"]) for txn in txns]

        cadence = classify_cadence(dates)
        if cadence is None:
            continue

        avg_interval = round(
            sum((dates[i] - dates[i - 1]).days for i in range(1, len(dates))) / (len(dates) - 1)
        )

        last_payment = dates[-1]
        if (today_ - last_payment).days > 90:
            continue

        next_payment = last_payment + timedelta(days=avg_interval)
        if today_ > next_payment + timedelta(days=30):
            next_payment = "Canceled"

        recurring.append(
            {
                "payee_id": txns[0]["payee_id"],
                "payee": txns[0]["canonical_name"],
                "cadence": cadence,
                "amount": Money(amount).amount,
                "occurrences": len(txns),
                "last_payment": last_payment,
                "next_payment": next_payment,
            }
        )

    return sorted(recurring, key=lambda x: x["next_payment"] == "Canceled")


def _subscription_account_id(conn) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE role = 'subscriptions'").fetchone()
    if not row:
        raise ValueError("No account is set up to hold subscriptions.")
    return row["id"]


def create_subscription(form) -> int:
    fields = _parse_form(form)

    with db.transaction() as conn:
        fields["first_seen_date"] = _derive_first_seen_date(fields["payee_id"], fields["amount_cents"])
        fields["account_id"] = _subscription_account_id(conn)
        return SubscriptionRepository(conn).create(fields)


def _parse_form(form):
    payee_id = forms.integer(form, "payee_id", "payee")
    cadence = forms.choice(form, "cadence", CADENCES, "cadence")
    amount = forms.positive_money(form, "amount", "Amount")

    with db.transaction() as conn:
        if PayeeRepository(conn).get(payee_id) is None:
            raise ValueError("Payee not found.")

    return {
        "payee_id": payee_id,
        "name": forms.text(form, "name") or None,
        "amount_cents": -amount.cents,
        "cadence": cadence,
        "notes": forms.text(form, "notes") or None,
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


def update_subscription(subscription_id: int, form):
    fields = _parse_form(form)
    fields["id"] = subscription_id

    with db.transaction() as conn:
        repo = SubscriptionRepository(conn)

        if repo.get(subscription_id) is None:
            raise ValueError("Subscription not found.")

        fields["account_id"] = _subscription_account_id(conn)
        repo.update(fields)


def cancel_subscription(subscription_id: int):
    with db.transaction() as conn:
        SubscriptionRepository(conn).cancel(subscription_id)


def reactivate_subscription(subscription_id: int):
    with db.transaction() as conn:
        SubscriptionRepository(conn).reactivate(subscription_id)


def delete_subscription(subscription_id: int):
    with db.transaction() as conn:
        repo = SubscriptionRepository(conn)

        if repo.charge_count(subscription_id):
            raise ValueError("Cancel this subscription instead, it already has matched charges.")

        repo.delete(subscription_id)
