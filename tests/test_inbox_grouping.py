from intent_ledger.accounting.inbox import _merge_similar_groups
from intent_ledger.importer.normalize import _fuzzy_group_key


def _fake_group(raw):
    return {
        "id": 1,
        "raw": raw,
        "count": 1,
        "latest_date": "2026-01-01",
        "total_amount": 0.0,
        "accounts": set(),
        "member_ids": [],
        "members": [],
    }


def test_distinct_payees_stay_separate_under_smart_grouping():
    descriptions = {
        "ALDEN": "PAY/Mr ALDEN/aldenriverside0/PAY/FIRST NATIONAL/64553011046/"
        "REF07c7ac8e5e34406490321d258d45edbc/",
        "Greenleaf1": "PAY/Greenleaf/paynet-112514809/PAY/PRAIRIE BANK/645594840922/"
        "REFcbc1df2365d41f5975fee1fbd0fc1d2/",
        "Greenleaf2": "PAY/Greenleaf/paynet-112514809/PAY/PRAIRIE BANK/608556257181/"
        "REFa5a36629675f4bc5bc7c8c313ef67d33/",
        "Meridian": "PAY/Meridian/q122008421/PAY/PRAIRIE BANK/643389009496/"
        "REFde48c8f375a94858b6a9b6dbf5ce9be5",
        "Farmstand": "PAY/Farmstand/paynet-121903170/PAY/PRAIRIE BANK/643326018780/"
        "REF46d76eecac1f428a9e74c76e2e2a598a",
        "JORDAN1": "PAY/JORDAN K/jkramer22@bankex/PAY/PRAIRIE BANK/643292953162/"
        "PNK17c420e0ff514b53adab50353205ee06",
        "JORDAN2": "PAY/JORDAN K/jkramer22@bankex/PAY/PRAIRIE BANK/643254961065/"
        "PNK6892a44d50d742fda89d88129c273773",
        "riley": "PAY/riley004/riley004west@/PAY/SUMMIT CREDIT UNION/606506306361/"
        "SMT8d74fd96c11549589af6d29c9b3c68c8/",
    }

    groups = {}
    for raw in descriptions.values():
        key = _fuzzy_group_key(raw)
        groups.setdefault(key, _fake_group(raw))

    merged = _merge_similar_groups(groups)

    # Distinct payees must not collapse into each other: the number of
    # groups after smart-merging should equal the number of distinct base
    # fuzzy keys (7: ALDEN, Greenleaf, Meridian, Farmstand, JORDAN,
    # riley all separate; the two Greenleaf/JORDAN pairs already share a
    # base key before smart-merging even runs).
    assert len(merged) == len(groups)

    assert _fuzzy_group_key(descriptions["Greenleaf1"]) == _fuzzy_group_key(descriptions["Greenleaf2"])
    assert _fuzzy_group_key(descriptions["JORDAN1"]) == _fuzzy_group_key(descriptions["JORDAN2"])
    assert _fuzzy_group_key(descriptions["Meridian"]) != _fuzzy_group_key(descriptions["Farmstand"])
    assert _fuzzy_group_key(descriptions["riley"]) != _fuzzy_group_key(descriptions["JORDAN1"])


def test_similar_payee_name_variants_still_merge_under_smart_grouping():
    groups = {
        "Greenleaf PRAIRIE BANK": _fake_group("a"),
        "Greenleaff PRAIRIE BANK": _fake_group("b"),
        "Farmstand PRAIRIE BANK": _fake_group("c"),
    }

    merged = _merge_similar_groups(groups)

    assert len(merged) == 2
