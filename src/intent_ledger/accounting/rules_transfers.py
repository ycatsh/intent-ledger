from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher

from intent_ledger.accounting.repositories.rules_transfers import TransferRuleRepository
from intent_ledger.accounting.rules import find_matching_rule, validate_pattern
from intent_ledger.db import db
from intent_ledger.domain.money import Money

DATE_WEIGHT = 1.0
DESCRIPTION_WEIGHT = 30.0
ABSTAIN_COST = 20.0
MAX_DATE_DIFF_DAYS = 45
DISQUALIFIED_COST = 1_000_000.0


def fetch_transfer_rules(conn):
    return TransferRuleRepository(conn).list_ordered_for_matching()


def is_transfer_candidate(rules, raw_description: str) -> bool:
    return find_matching_rule(rules, raw_description) is not None


def _pair_cost(a, b):
    if a["account_id"] == b["account_id"]:
        return DISQUALIFIED_COST

    date_diff = abs((date.fromisoformat(a["posted_date"]) - date.fromisoformat(b["posted_date"])).days)
    if date_diff > MAX_DATE_DIFF_DAYS:
        return DISQUALIFIED_COST

    similarity = SequenceMatcher(None, a["raw_description"], b["raw_description"]).ratio()
    return date_diff * DATE_WEIGHT + (1 - similarity) * DESCRIPTION_WEIGHT


def match_transfers(candidates):
    buckets = defaultdict(lambda: {"out": [], "in": []})

    for txn in candidates:
        if txn["amount_cents"] == 0:
            continue
        key = abs(txn["amount_cents"])
        side = "out" if txn["amount_cents"] < 0 else "in"
        buckets[key][side].append(txn)

    pairs = {}
    for bucket in buckets.values():
        if not bucket["out"] or not bucket["in"]:
            continue
        pairs.update(_match_bucket(bucket["out"], bucket["in"]))

    return pairs


def _match_bucket(outs, ins):
    size = max(len(outs), len(ins))
    cost = [[ABSTAIN_COST] * size for _ in range(size)]

    for i, out_txn in enumerate(outs):
        for j, in_txn in enumerate(ins):
            cost[i][j] = _pair_cost(out_txn, in_txn)

    assignment = _hungarian(cost)

    pairs = {}
    for i, j in enumerate(assignment):
        if i >= len(outs) or j >= len(ins) or cost[i][j] >= ABSTAIN_COST:
            continue
        out_txn, in_txn = outs[i], ins[j]
        pairs[out_txn["id"]] = in_txn
        pairs[in_txn["id"]] = out_txn

    return pairs


def _hungarian(cost):
    n = len(cost)
    inf = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (n + 1)
        used = [False] * (n + 1)

        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = -1

            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j

            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta

            j0 = j1
            if p[j0] == 0:
                break

        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    result = [0] * n
    for j in range(1, n + 1):
        if p[j]:
            result[p[j] - 1] = j - 1

    return result


def get_transfer_rules():
    with db.transaction() as conn:
        return TransferRuleRepository(conn).list_for_display()


def get_transfer_rule(rule_id):
    with db.transaction() as conn:
        return TransferRuleRepository(conn).get(rule_id)


def add_transfer_rule(form):
    pattern = form.get("pattern", "").strip()
    if not pattern:
        raise ValueError("Pattern is required.")

    match_type = form.get("match_type", "contains")
    validate_pattern(match_type, pattern)

    with db.transaction() as conn:
        return TransferRuleRepository(conn).create(match_type, pattern)


def update_transfer_rule(rule_id, form):
    pattern = form.get("pattern", "").strip()
    if not pattern:
        raise ValueError("Pattern is required.")

    match_type = form.get("match_type", "contains")
    validate_pattern(match_type, pattern)

    with db.transaction() as conn:
        TransferRuleRepository(conn).update(rule_id, match_type, pattern)


def delete_transfer_rule(rule_id):
    with db.transaction() as conn:
        TransferRuleRepository(conn).delete(rule_id)


def preview_transfer_matches(rule_id, limit=200):
    with db.transaction() as conn:
        rule = TransferRuleRepository(conn).get(rule_id)

        if rule is None:
            raise ValueError("Transfer rule not found.")

        transactions = conn.execute("""
            SELECT id, account_id, posted_date, amount_cents, raw_description
            FROM transactions
            ORDER BY posted_date DESC, id DESC
        """).fetchall()

        rules = fetch_transfer_rules(conn)
        accounts_by_id = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM accounts")}

    candidates = [t for t in transactions if is_transfer_candidate(rules, t["raw_description"])]
    counterparts = match_transfers(candidates)

    pairs = []
    unmatched = 0
    seen_ids = set()

    for txn in candidates:
        if not is_transfer_candidate([rule], txn["raw_description"]) or txn["id"] in seen_ids:
            continue

        counterpart = counterparts.get(txn["id"])
        if counterpart is None:
            unmatched += 1
            continue

        seen_ids.add(txn["id"])
        seen_ids.add(counterpart["id"])

        source, target = (txn, counterpart) if txn["amount_cents"] < 0 else (counterpart, txn)
        pairs.append(
            {
                "from_account": accounts_by_id.get(source["account_id"]),
                "to_account": accounts_by_id.get(target["account_id"]),
                "posted_date": source["posted_date"],
                "raw_description": source["raw_description"],
                "amount": Money(abs(source["amount_cents"])).amount,
            }
        )

        if len(pairs) >= limit:
            break

    return {"pairs": pairs, "unmatched": unmatched}
