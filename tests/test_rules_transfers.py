import itertools
import random

from intent_ledger.accounting.rules_transfers import _hungarian, match_transfers


def _brute_force_min_cost(cost):
    n = len(cost)
    return min(sum(cost[i][perm[i]] for i in range(n)) for perm in itertools.permutations(range(n)))


def test_hungarian_matches_brute_force_on_random_matrices():
    random.seed(0)
    for _ in range(25):
        n = random.randint(1, 6)
        cost = [[random.randint(0, 50) for _ in range(n)] for _ in range(n)]
        assignment = _hungarian(cost)

        assert sorted(assignment) == list(range(n))
        total = sum(cost[i][assignment[i]] for i in range(n))
        assert total == _brute_force_min_cost(cost)


def _txn(id, account_id, posted_date, amount_cents, description):
    return {
        "id": id,
        "account_id": account_id,
        "posted_date": posted_date,
        "amount_cents": amount_cents,
        "raw_description": description,
    }


def test_match_transfers_picks_globally_optimal_pairing_over_nearest_date():
    out1 = _txn(1, 100, "2026-01-01", -1000, "TRANSFER")
    out2 = _txn(2, 100, "2026-01-20", -1000, "TRANSFER")
    in1 = _txn(3, 200, "2026-01-01", 1000, "TRANSFER")
    in2 = _txn(4, 200, "2026-01-20", 1000, "TRANSFER")

    pairs = match_transfers([out1, out2, in1, in2])

    assert pairs[1]["id"] == 3
    assert pairs[3]["id"] == 1
    assert pairs[2]["id"] == 4
    assert pairs[4]["id"] == 2


def test_match_transfers_rejects_same_account_pairs():
    out1 = _txn(1, 100, "2026-01-01", -1000, "TRANSFER")
    in1 = _txn(2, 100, "2026-01-01", 1000, "TRANSFER")

    pairs = match_transfers([out1, in1])

    assert pairs == {}


def test_match_transfers_abstains_when_dates_are_too_far_apart():
    out1 = _txn(1, 100, "2026-01-01", -1000, "TRANSFER")
    in1 = _txn(2, 200, "2026-06-01", 1000, "TRANSFER")

    pairs = match_transfers([out1, in1])

    assert pairs == {}


def test_match_transfers_abstains_on_dissimilar_description_same_day():
    out1 = _txn(1, 100, "2026-01-01", -1000, "WITHDRAWAL")
    in1 = _txn(2, 200, "2026-01-01", 1000, "DEPOSIT")

    pairs = match_transfers([out1, in1])

    assert pairs == {}


def test_match_transfers_tolerates_a_day_of_drift_with_similar_description():
    out1 = _txn(1, 100, "2026-01-20", -18000, "ONLINE PAYMENT TO CREDIT CARD")
    in1 = _txn(2, 200, "2026-01-21", 18000, "PAYMENT RECEIVED - THANK YOU")

    pairs = match_transfers([out1, in1])

    assert pairs[1]["id"] == 2
    assert pairs[2]["id"] == 1


def test_match_transfers_leaves_unequal_counts_partially_unmatched():
    outs = [_txn(i, 100, "2026-01-01", -1000, "TRANSFER") for i in range(1, 4)]
    ins = [_txn(i, 200, "2026-01-01", 1000, "TRANSFER") for i in range(4, 6)]

    pairs = match_transfers(outs + ins)

    matched_out_ids = {out["id"] for out in outs if out["id"] in pairs}
    assert len(matched_out_ids) == 2
    assert all(ins_id in pairs for ins_id in (4, 5))
