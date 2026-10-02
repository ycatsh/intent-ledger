from pathlib import Path

SHOWN_ROW_ERRORS = 3


def rebuild_impact_message(changed: int) -> str:
    if not changed:
        return " No transactions were affected."
    return f" {changed} transaction{'' if changed == 1 else 's'} recategorized."


def import_problems(summaries: list[dict]) -> list[str]:
    problems = []

    for summary in summaries:
        name = Path(summary["statement"]).name

        if summary.get("error"):
            problems.append(f"{name}: {summary['error']}")

        row_errors = summary["row_errors"]
        if row_errors:
            shown = "; ".join(row_errors[:SHOWN_ROW_ERRORS])
            more = len(row_errors) - SHOWN_ROW_ERRORS
            problems.append(
                f"{name}: skipped {len(row_errors)} row(s): {shown}"
                + (f", and {more} more." if more > 0 else "")
            )

    return problems
