import csv
import io
import re
import zipfile
from calendar import month_name
from collections import defaultdict
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from intent_ledger.accounting.accounts import (
    get_account,
    get_active_accounts,
    get_ledger_entries,
    get_running_balances,
)
from intent_ledger.accounting.projects import get_project
from intent_ledger.analytics.reports import (
    get_categories,
    get_counterparty_lines,
    get_equity_lines,
    get_income_expense_lines,
    get_income_sources,
    get_payees,
    get_summary,
)
from intent_ledger.db import db
from intent_ledger.settings import get_base_currency, get_export_format, today

HEADER_FILL = PatternFill(fill_type="solid", start_color="217346", end_color="217346")
HEADER_FONT = Font(bold=True, color="FFFFFF")
SPLIT_FILL = PatternFill(fill_type="solid", start_color="FBF3DB", end_color="FBF3DB")
CATEGORY_FILL = PatternFill(fill_type="solid", start_color="F2F4F6", end_color="F2F4F6")
SUBTOTAL_BORDER = Border(top=Side(style="thin", color="C9CFD6"))
GRAND_TOTAL_BORDER = Border(top=Side(style="thin", color="C9CFD6"))
BOLD = Font(bold=True)
GRAND_FONT = Font(bold=True, size=12)

FORMATS = {
    "money": "#,##0.00",
    "signed": "+#,##0.00;-#,##0.00",
    "date": "yyyy-mm-dd",
    "count": "#,##0",
    "percent": '0.0"%"',
}
NUMERIC = ("money", "signed", "count", "percent")
SUMMARY_WIDTHS = {"A": 28, "B": 44}
HIDDEN_COLUMNS = ("Description",)

DEFINITIONS = [
    ("Income and expenses", "text", "Refunds and reversals are subtracted"),
    ("Money in and money out", "text", "Transfers between accounts are left out"),
    ("Highlighted rows", "text", "Parts of a split transaction"),
    ("Description column", "text", "Hidden. Unhide it to read the bank text"),
]


def export_monthly_report(year, month):
    return _period_report(
        f"{year}-{month:02d}_income_expenses", f"{month_name[month]} {year}", year=year, month=month
    )


def export_yearly_report(year):
    return _period_report(f"{year}_income_expenses", str(year), year=year)


def _period_report(stem, label, **scope):
    workbook = Workbook()
    _summary(workbook.active, [("Period", "text", label), _generated()], get_summary(**scope))
    _breakdowns(workbook, scope)
    return _save(workbook, stem)


def export_project_report(project_id):
    project = get_project(project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found.")

    summary = get_summary(project_id=project_id)
    facts = [
        ("Project", "text", project["name"]),
        ("Type", "text", project["type"]),
        ("Status", "text", "Archived" if project["archived"] else "Active"),
        ("Starts", "date", project["start_date"]),
        ("Ends", "date", project["end_date"]),
        ("Notes", "text", project["notes"]),
        _generated(),
    ]

    budget = project["budget_cents"]
    extra = []
    if budget:
        extra = [
            None,
            ("Budget", "money", budget),
            ("Spent", "money", summary["expenses_cents"]),
            ("Budget left", "signed", budget - summary["expenses_cents"], "total"),
            ("Budget used %", "percent", 100 * summary["expenses_cents"] / budget),
        ]

    workbook = Workbook()
    _summary(workbook.active, facts, summary, extra)
    _breakdowns(workbook, {"project_id": project_id})
    return _save(workbook, f"{today():%Y-%m}_project_{_slug(project['name'])}")


def _generated():
    return ("Generated", "date", today().isoformat())


def _summary(sheet, facts, summary, extra=()):
    sheet.title = "Summary"
    _facts(
        sheet,
        [
            *facts,
            None,
            ("Income", "money", summary["income_cents"]),
            ("Expenses", "money", summary["expenses_cents"]),
            ("Budgeted", "money", summary["budgeted_cents"], "part"),
            ("Unbudgeted", "money", summary["unbudgeted_cents"], "part"),
            ("Net", "signed", summary["net_cents"], "total"),
            None,
            ("Money in", "money", summary["inflow_cents"]),
            ("Money out", "money", summary["outflow_cents"]),
            ("Cash flow", "signed", summary["cashflow_cents"], "total"),
            None,
            ("Transactions", "count", summary["transactions"]),
            *extra,
            None,
            *DEFINITIONS,
        ],
    )


def _breakdowns(workbook, scope):
    for title, key, rows in (
        ("Categories", "category", get_categories(**scope)),
        ("Payees", "payee", get_payees(**scope)),
    ):
        _table(
            workbook.create_sheet(title),
            [(key.capitalize(), "text"), ("Amount", "money"), ("Transactions", "count"), ("%", "percent")],
            [[r[key], r["amount_cents"], r["transactions"], r["percent"]] for r in rows],
            ["Total", _total(rows), None, _whole(rows)],
        )

    sources = get_income_sources(**scope)
    _table(
        workbook.create_sheet("Income sources"),
        [("Payee", "text"), ("Account", "text"), ("Type", "text"), ("Amount", "money"), ("%", "percent")],
        [[r["payee"], r["account"], r["kind"], r["amount_cents"], r["percent"]] for r in sources],
        ["Total", None, None, _total(sources), _whole(sources)],
    )

    lines = get_income_expense_lines(**scope)
    _table(
        workbook.create_sheet("Transactions"),
        [
            ("Date", "date"),
            ("Account", "text"),
            ("Payee", "text"),
            ("Category", "text"),
            ("Income", "money"),
            ("Expense", "money"),
            ("Description", "text"),
        ],
        [
            [
                line["date"],
                line["account"],
                line["payee"],
                line["category"],
                line["amount_cents"] if line["type"] == "income" else None,
                line["amount_cents"] if line["type"] == "expense" else None,
                line["description"],
            ]
            for line in lines
        ],
        [
            "Total",
            None,
            None,
            None,
            sum(line["amount_cents"] for line in lines if line["type"] == "income"),
            sum(line["amount_cents"] for line in lines if line["type"] == "expense"),
            None,
        ],
        highlight=[bool(line["is_split"]) for line in lines],
    )


def export_account_statement(account_id, search=None):
    account = get_account(account_id)
    if account is None:
        raise ValueError(f"Account {account_id} not found.")

    entries = get_ledger_entries(account_id, search=search, sort="date_asc")
    with db.transaction() as conn:
        balances = get_running_balances(conn, account_id)

    lines = []
    for entry in entries:
        parts = _counter_parts(entry)
        for index, (category, kind, cents, note) in enumerate(parts):
            last_part = index == len(parts) - 1
            balance = balances[entry["transaction_hash"]] if last_part else None
            line = {**entry, "description": note} if note else entry
            lines.append((line, category, kind, cents, balance))

    money_in = sum(cents for _, _, _, cents, _ in lines if cents > 0)
    money_out = -sum(cents for _, _, _, cents, _ in lines if cents < 0)
    first, last = (entries[0]["date"], entries[-1]["date"]) if entries else (None, None)

    facts = [
        ("Account", "text", account["name"]),
        ("Institution", "text", account["institution"]),
        ("Account number", "text", f"XXXX{account['last4']}" if account["last4"] else None),
        ("Type", "text", account["type"].capitalize()),
        ("Currency", "text", _currency()),
        ("From", "date", first),
        ("To", "date", last),
        ("Filter", "text", search or "All transactions"),
        _generated(),
        None,
    ]

    if search:
        facts += [
            ("Money in", "money", money_in),
            ("Money out", "money", money_out),
            ("Net", "signed", money_in - money_out, "total"),
            None,
            ("Totals", "text", "Matching transactions only"),
        ]
    else:
        closing = balances[entries[-1]["transaction_hash"]] if entries else account["balance_cents"]
        opening = closing - money_in + money_out
        if entries and balances[entries[0]["transaction_hash"]] - entries[0]["amount_cents"] != opening:
            raise RuntimeError(f"The statement for {account['name']} does not reconcile.")
        facts += [
            ("Opening balance", "signed", opening),
            ("Money in", "money", money_in),
            ("Money out", "money", money_out),
            ("Closing balance", "signed", closing, "total"),
        ]

    workbook = Workbook()
    workbook.active.title = "Summary"
    _facts(
        workbook.active,
        [*facts, None, ("Transactions", "count", len(entries)), None, DEFINITIONS[2]],
    )

    _table(
        workbook.create_sheet("Transactions"),
        [
            ("Date", "date"),
            ("Payee", "text"),
            ("Category", "text"),
            ("Type", "text"),
            ("Description", "text"),
            ("Money in", "money"),
            ("Money out", "money"),
            ("Balance", "signed"),
        ],
        [
            [
                entry["date"],
                entry["payee"],
                category,
                kind,
                entry["description"],
                cents if cents > 0 else None,
                -cents if cents < 0 else None,
                balance,
            ]
            for entry, category, kind, cents, balance in lines
        ],
        ["Total", None, None, None, None, money_in, money_out, None],
        highlight=[bool(entry["has_split"]) for entry, *_ in lines],
    )

    _by_category(workbook.create_sheet("By category"), lines)

    month = (date.fromisoformat(last) if last else today()).strftime("%Y-%m")
    return _save(workbook, f"{month}_statement_{_slug(account['name'])}")


def _by_category(sheet, lines):
    groups = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for entry, category, kind, cents, _ in lines:
        totals = groups[category, kind][entry["payee"] or ""]
        totals[0] += cents
        totals[1] += 1

    columns = [("Category", "text"), ("Payee", "text"), ("Amount", "signed"), ("Transactions", "count")]
    _header(sheet, 1, 1, columns)
    sheet.sheet_properties.outlinePr.summaryBelow = True

    row = 1
    grand_cents = grand_count = 0
    for (category, kind), payees in sorted(groups.items()):
        row += 1
        label = f"{category} ({kind.lower()})" if kind else category
        for column in range(1, 5):
            _put(sheet, row, column, "text", label if column == 1 else None, BOLD, fill=CATEGORY_FILL)

        for payee, (cents, count) in sorted(payees.items(), key=lambda item: (-abs(item[1][0]), item[0])):
            row += 1
            _put(sheet, row, 2, "text", payee).alignment = Alignment(indent=1)
            _put(sheet, row, 3, "signed", cents)
            _put(sheet, row, 4, "count", count)
            sheet.row_dimensions[row].outlineLevel = 1

        cents = sum(total for total, _ in payees.values())
        count = sum(n for _, n in payees.values())
        row += 1
        subtotal = Font(bold=True, italic=True)
        _put(sheet, row, 1, "text", None, border=SUBTOTAL_BORDER)
        _put(sheet, row, 2, "text", "Total", subtotal, SUBTOTAL_BORDER).alignment = Alignment(indent=1)
        _put(sheet, row, 3, "signed", cents, subtotal, SUBTOTAL_BORDER)
        _put(sheet, row, 4, "count", count, subtotal, SUBTOTAL_BORDER)
        grand_cents += cents
        grand_count += count
        row += 1

    row += 1
    _put(sheet, row, 1, "text", "Grand total", GRAND_FONT, GRAND_TOTAL_BORDER)
    _put(sheet, row, 2, "text", None, border=GRAND_TOTAL_BORDER)
    _put(sheet, row, 3, "signed", grand_cents, GRAND_FONT, GRAND_TOTAL_BORDER)
    _put(sheet, row, 4, "count", grand_count, GRAND_FONT, GRAND_TOTAL_BORDER)
    sheet.freeze_panes = "A2"


def _counter_parts(entry):
    if entry["has_split"]:
        return [
            (split["account"], _type_label(split["type"]), split["amount_cents"], split["note"])
            for split in entry["splits"]
        ]
    category = entry["category"] or "Unknown"
    return [(category, _type_label(entry["category_type"]), entry["amount_cents"], None)]


def _type_label(account_type):
    return (account_type or "").capitalize() or None


def export_balance_sheet():
    by_type = defaultdict(list)
    for account in get_active_accounts():
        by_type[account["type"]].append(account)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Balance sheet"

    _header(sheet, 1, 1, [("Account", "text"), ("Balance", "signed")])
    _put(sheet, 2, 1, "text", "As of")
    _put(sheet, 2, 2, "date", today().isoformat())
    row, assets = _section(sheet, 4, "Assets", by_type["asset"], 1)
    row, liabilities = _section(sheet, row, "Liabilities", by_type["liability"], -1)
    _put(sheet, row, 1, "text", "Net worth", GRAND_FONT, GRAND_TOTAL_BORDER)
    _put(sheet, row, 2, "signed", assets - liabilities, GRAND_FONT, GRAND_TOTAL_BORDER)
    sheet.freeze_panes = "A2"

    equity = [
        [
            line["date"],
            line["equity"],
            line["account"],
            line["payee"],
            line["description"],
            -line["amount_cents"] if line["amount_cents"] < 0 else None,
            line["amount_cents"] if line["amount_cents"] > 0 else None,
        ]
        for line in get_equity_lines()
    ]
    counterparties = [
        [
            line["date"],
            line["counterparty"],
            line["account"],
            line["payee"],
            line["category"],
            line["description"],
            -line["amount_cents"] if line["amount_cents"] < 0 else None,
            line["amount_cents"] if line["amount_cents"] > 0 else None,
        ]
        for line in get_counterparty_lines()
    ]

    equity_in, equity_out = _column(equity, 5), _column(equity, 6)
    row = _table(
        sheet,
        [("Equity (memo)", "text"), ("Money in", "money"), ("Money out", "money"), ("Net in", "signed")],
        [[name, inflow, outflow, inflow - outflow] for name, inflow, outflow, _ in _memo(equity, 1, 5)],
        ["Total", equity_in, equity_out, equity_in - equity_out],
        top=4,
        left=4,
    )

    spent, received = _column(counterparties, 6), _column(counterparties, 7)
    row = _table(
        sheet,
        [
            ("Counter-party (memo)", "text"),
            ("Spent", "money"),
            ("Received", "money"),
            ("Net spent", "signed"),
            ("Transactions", "count"),
        ],
        [[name, out, back, out - back, count] for name, out, back, count in _memo(counterparties, 1, 6)],
        ["Total", spent, received, spent - received, len(counterparties)],
        top=row,
        left=4,
    )
    _facts(sheet, [("Memos", "text", "All-time totals, not part of net worth")], row, left=4)

    _table(
        workbook.create_sheet("Equity detail"),
        [
            ("Date", "date"),
            ("Equity account", "text"),
            ("Account", "text"),
            ("Payee", "text"),
            ("Description", "text"),
            ("Money in", "money"),
            ("Money out", "money"),
        ],
        equity,
        ["Total", None, None, None, None, equity_in, equity_out],
    )
    _table(
        workbook.create_sheet("Counter-party detail"),
        [
            ("Date", "date"),
            ("Counter-party", "text"),
            ("Account", "text"),
            ("Payee", "text"),
            ("Category", "text"),
            ("Description", "text"),
            ("Spent", "money"),
            ("Received", "money"),
        ],
        counterparties,
        ["Total", None, None, None, None, None, spent, received],
    )

    return _save(workbook, f"{today():%Y-%m}_balance_sheet")


def _section(sheet, row, title, accounts, sign):
    _put(sheet, row, 1, "text", title, BOLD, fill=CATEGORY_FILL)
    _put(sheet, row, 2, "text", None, fill=CATEGORY_FILL)
    row += 1
    total = 0

    for account in sorted(accounts, key=lambda a: -sign * a["balance_cents"]):
        _put(sheet, row, 1, "text", account["name"]).alignment = Alignment(indent=1)
        _put(sheet, row, 2, "signed", sign * account["balance_cents"])
        total += sign * account["balance_cents"]
        row += 1

    _put(sheet, row, 1, "text", f"Total {title.lower()}", BOLD, SUBTOTAL_BORDER)
    _put(sheet, row, 2, "signed", total, BOLD, SUBTOTAL_BORDER)
    return row + 2, total


def _memo(lines, name, first):
    totals = defaultdict(lambda: [0, 0, 0])
    for line in lines:
        entry = totals[line[name]]
        entry[0] += line[first] or 0
        entry[1] += line[first + 1] or 0
        entry[2] += 1
    return sorted(([key, *values] for key, values in totals.items()), key=lambda m: (-m[1], m[0]))


def _facts(sheet, facts, row=1, left=1):
    start = row
    for fact in facts:
        if fact is not None:
            label, kind, value, *style = fact
            style = style[0] if style else None
            font, border = (BOLD, SUBTOTAL_BORDER) if style == "total" else (None, None)
            label_cell = _put(sheet, row, left, "text", label, font, border)
            if style == "part":
                label_cell.alignment = Alignment(indent=1)
            _put(sheet, row, left + 1, kind, value, font, border)
        row += 1

    if (start, left) == (1, 1):
        for cell in sheet[1]:
            cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        sheet.freeze_panes = "A2"
    return row + 1


def _header(sheet, row, column, columns):
    for offset, (title, kind) in enumerate(columns):
        cell = sheet.cell(row=row, column=column + offset, value=title)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        if kind in NUMERIC:
            cell.alignment = Alignment(horizontal="right")


def _table(sheet, columns, rows, total, top=1, left=1, highlight=()):
    _header(sheet, top, left, columns)

    row = top
    for index, values in enumerate(rows):
        row += 1
        fill = SPLIT_FILL if index < len(highlight) and highlight[index] else None
        for offset, ((_, kind), value) in enumerate(zip(columns, values, strict=True)):
            _put(sheet, row, left + offset, kind, value, fill=fill)

    for offset, (title, _) in enumerate(columns):
        if title in HIDDEN_COLUMNS:
            dimension = sheet.column_dimensions[sheet.cell(row=top, column=left + offset).column_letter]
            dimension.outlineLevel, dimension.hidden = 1, True

    if (top, left) == (1, 1):
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{sheet.cell(row=max(row, 2), column=len(columns)).coordinate}"

    row += 1
    for offset, ((_, kind), value) in enumerate(zip(columns, total, strict=True)):
        kind = "text" if isinstance(value, str) else kind
        _put(sheet, row, left + offset, kind, value, BOLD, SUBTOTAL_BORDER)

    return row + 2


def _put(sheet, row, column, kind, value, font=None, border=None, fill=None):
    if kind in ("money", "signed") and value is not None:
        value = value / 100
    elif kind == "date" and value:
        value = date.fromisoformat(value[:10])
    elif value == "":
        value = None

    cell = sheet.cell(row=row, column=column, value=value)
    if isinstance(value, str):
        cell.data_type = "s"
    if kind in FORMATS:
        cell.number_format = FORMATS[kind]
    if font:
        cell.font = font
    if border:
        cell.border = border
    if fill:
        cell.fill = fill
    return cell


def _total(rows):
    return sum(r["amount_cents"] for r in rows)


def _whole(rows):
    return 100.0 if _total(rows) > 0 else None


def _column(rows, index):
    return sum(row[index] or 0 for row in rows)


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _currency():
    with db.transaction() as conn:
        return get_base_currency(conn)


def _save(workbook, stem) -> tuple[str, bytes]:
    with db.transaction() as conn:
        export_format = get_export_format(conn)

    if export_format == "csv":
        return f"{stem}.zip", _csv_archive(workbook)

    for sheet in workbook.worksheets:
        if sheet.title == "Summary":
            for letter, width in SUMMARY_WIDTHS.items():
                sheet.column_dimensions[letter].width = width
            continue

        widths = defaultdict(int)
        for cells in sheet.iter_rows():
            for cell in cells:
                if cell.value is not None:
                    widths[cell.column_letter] = max(widths[cell.column_letter], len(_shown(cell)))
        for letter, width in widths.items():
            sheet.column_dimensions[letter].width = min(max(width + 3, 12), 80)

    content = io.BytesIO()
    workbook.save(content)
    return f"{stem}.xlsx", content.getvalue()


def _csv_archive(workbook) -> bytes:
    """One CSV per sheet in a zip, since a CSV file holds a single sheet."""
    archive_buffer = io.BytesIO()

    with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for sheet in workbook.worksheets:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            for row in sheet.iter_rows(values_only=True):
                writer.writerow(["" if v is None else v for v in row])
            archive.writestr(f"{_slug(sheet.title)}.csv", buffer.getvalue())

    return archive_buffer.getvalue()


def _shown(cell):
    if isinstance(cell.value, date):
        return "0000-00-00"
    if isinstance(cell.value, float):
        return f"+{abs(cell.value):,.2f}"
    return str(cell.value)
