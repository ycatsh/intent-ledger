def rebuild_impact_message(changed: int) -> str:
    if not changed:
        return " No transactions were affected."
    return f" {changed} transaction{'' if changed == 1 else 's'} recategorized."
