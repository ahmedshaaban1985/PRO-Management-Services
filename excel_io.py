# -*- coding: utf-8 -*-
"""Excel export / import (openpyxl)."""
import datetime as dt

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import core

BLACK = "141414"
GOLD = "C9A227"
RED = "F8C9CF"
AMBER = "FFE4B0"
THIN = Side(style="thin", color="D0D0D0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# (key, arabic header, english header, kind)
EMP_COLUMNS = [
    ("code", "رقم الموظف", "Employee No.", "text"),
    ("name", "اسم الموظف", "Employee Name", "text"),
    ("job_title", "المهنة", "Job Title", "text"),
    ("nationality", "الجنسية", "Nationality", "text"),
    ("dob", "تاريخ الميلاد", "Date of Birth", "date"),
    ("phone", "رقم الهاتف", "Phone", "text"),
    ("qid_number", "رقم البطاقة الشخصية", "QID No.", "text"),
    ("qid_expiry", "انتهاء البطاقة", "QID Expiry", "expiry"),
    ("passport_number", "رقم الجواز", "Passport No.", "text"),
    ("passport_expiry", "انتهاء الجواز", "Passport Expiry", "expiry"),
    ("health_number", "رقم الشهادة الصحية", "Health Card No.", "text"),
    ("health_expiry", "انتهاء الشهادة الصحية", "Health Card Expiry", "expiry"),
    ("contract_start", "بداية العقد", "Contract Start", "date"),
    ("contract_expiry", "انتهاء العقد", "Contract Expiry", "expiry"),
    ("bank_name", "اسم البنك", "Bank", "text"),
    ("bank_account", "رقم الحساب", "Account No.", "text"),
    ("iban", "الآيبان IBAN", "IBAN", "text"),
    ("salary", "الراتب", "Salary", "money"),
    ("status", "الحالة", "Status", "status"),
    ("notes", "ملاحظات", "Notes", "text"),
]

# extra header spellings accepted when importing someone else's sheet
ALIASES = {
    "name": ["الاسم", "name", "اسم", "full name", "employee"],
    "qid_number": ["رقم البطاقة", "qid", "id no", "رقم الإقامة", "رقم الاقامة", "qid number"],
    "qid_expiry": ["انتهاء البطاقة الشخصية", "تاريخ انتهاء البطاقة", "انتهاء الإقامة",
                   "انتهاء الاقامة", "qid expiry", "id expiry"],
    "passport_number": ["رقم جواز السفر", "passport", "passport no", "passport number"],
    "passport_expiry": ["انتهاء جواز السفر", "تاريخ انتهاء الجواز", "passport expiry"],
    "health_expiry": ["انتهاء الشهادة الصحية", "انتهاء الفحص الطبي", "health expiry",
                      "health card expiry", "medical expiry"],
    "health_number": ["رقم الفحص الطبي", "health card", "health no"],
    "dob": ["تاريخ الميلاد", "date of birth", "birth date", "dob"],
    "phone": ["الهاتف", "الجوال", "رقم الجوال", "mobile", "phone number", "tel"],
    "salary": ["الراتب", "salary", "basic salary", "الراتب الأساسي"],
    "iban": ["iban", "ايبان", "الايبان", "الآيبان"],
    "bank_account": ["رقم الحساب البنكي", "account", "account number", "account no"],
    "bank_name": ["البنك", "bank", "bank name"],
    "job_title": ["المهنة", "الوظيفة", "job", "profession", "occupation", "designation"],
    "nationality": ["الجنسية", "nationality"],
    "contract_expiry": ["انتهاء العقد", "contract end", "contract expiry"],
    "contract_start": ["بداية العقد", "contract start", "joining date", "تاريخ الالتحاق"],
    "notes": ["ملاحظات", "notes", "remarks"],
}


def _header_style(cell):
    cell.font = Font(bold=True, color=GOLD)
    cell.fill = PatternFill("solid", fgColor=BLACK)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = BORDER


def _title_rows(ws, db, lang, title, subtitle, ncols):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=ncols)
    ws["A1"] = db.company_name(lang)
    ws["A1"].font = Font(bold=True, size=14, color=GOLD)
    ws["A1"].fill = PatternFill("solid", fgColor=BLACK)
    ws["A2"] = title
    ws["A2"].font = Font(bold=True, size=12)
    ws["A3"] = subtitle
    ws["A3"].font = Font(size=10, color="666666")
    for r in (1, 2, 3):
        ws.cell(r, 1).alignment = Alignment(horizontal="center")
    ws.row_dimensions[1].height = 24


def _autowidth(ws, first_row, ncols, minimum=10, maximum=40):
    for c in range(1, ncols + 1):
        best = minimum
        for r in range(first_row, ws.max_row + 1):
            v = ws.cell(r, c).value
            if v is not None:
                best = max(best, min(maximum, len(str(v)) + 3))
        ws.column_dimensions[get_column_letter(c)].width = best


def export_employees(db, path, client_id=None, lang="ar", status="active", salaries=True):
    ar = lang != "en"
    wb = Workbook()
    ws = wb.active
    ws.title = "الموظفين" if ar else "Employees"
    ws.sheet_view.rightToLeft = ar
    alert = db.get_int("alert_days", 30)

    cols = list(EMP_COLUMNS)
    if not salaries:
        cols = [c for c in cols if c[0] not in ("salary", "bank_name", "bank_account", "iban")]
    if not client_id:
        cols.insert(0, ("client_name", "الشركة", "Company", "text"))
    heads = ["م" if ar else "#"] + [c[1] if ar else c[2] for c in cols]
    n = len(heads)
    if client_id:
        c = db.client(client_id)
        title = (f"كشف موظفي: {c['name']}" if ar else f"Employees list: {c['name']}")
        sub_parts = [c["code"]]
        if c["cr_number"]:
            sub_parts.append(("سجل تجاري: " if ar else "CR: ") + c["cr_number"])
        if c["est_number"]:
            sub_parts.append(("قيد المنشأة: " if ar else "Est. No: ") + c["est_number"])
    else:
        title = "كشف موظفي كل الشركات" if ar else "All companies - employees list"
        sub_parts = []
    sub_parts.append(("بتاريخ: " if ar else "Date: ") + core.iso(core.today()))
    _title_rows(ws, db, lang, title, "   |   ".join(sub_parts), n)

    hr = 5
    for j, h in enumerate(heads, 1):
        _header_style(ws.cell(hr, j, h))
    ws.row_dimensions[hr].height = 30

    rows = db.employees(client_id=client_id, status=status)
    total_salary = 0.0
    for i, e in enumerate(rows, 1):
        r = hr + i
        ws.cell(r, 1, i)
        for j, (key, _, _, kind) in enumerate(cols, 2):
            v = e[key]
            cell = ws.cell(r, j)
            if kind in ("date", "expiry"):
                d = core.parse_date(v)
                cell.value = d if d else None
                cell.number_format = "yyyy-mm-dd"
                if kind == "expiry" and d:
                    dl = core.days_left(v)
                    if dl is not None and dl < 0:
                        cell.fill = PatternFill("solid", fgColor=RED)
                    elif dl is not None and dl <= alert:
                        cell.fill = PatternFill("solid", fgColor=AMBER)
            elif kind == "money":
                cell.value = float(v or 0)
                cell.number_format = "#,##0.00"
                total_salary += float(v or 0)
            elif kind == "status":
                cell.value = ({"active": "على رأس العمل", "left": "غادر"} if ar
                              else {"active": "Active", "left": "Left"}).get(v, v)
            else:
                cell.value = v or ""
                cell.number_format = "@"          # keep long numbers as text
            cell.border = BORDER
        ws.cell(r, 1).border = BORDER
    last = hr + len(rows)
    tr = last + 1
    if salaries:
        sal_col = [k for k, *_ in cols].index("salary") + 2
        ws.cell(tr, sal_col - 1, "إجمالي الرواتب" if ar else "Total salaries").font = Font(bold=True)
        tc = ws.cell(tr, sal_col, total_salary)
        tc.font = Font(bold=True)
        tc.number_format = "#,##0.00"
    ws.cell(tr + 1, 1, ("عدد الموظفين: " if ar else "Employees: ") + str(len(rows)))
    ws.cell(tr + 2, 1, ("الأحمر = منتهي، البرتقالي = ينتهي خلال " if ar else
                        "Red = expired, orange = expires within ") + str(alert)
            + (" يوم" if ar else " days")).font = Font(size=9, color="666666")
    ws.freeze_panes = ws.cell(hr + 1, 3 if not client_id else 3)
    if rows:
        ws.auto_filter.ref = f"A{hr}:{get_column_letter(n)}{last}"
    _autowidth(ws, hr, n)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToHeight = 0
    wb.save(path)
    return len(rows)


def employees_template(path, lang="ar"):
    ar = lang != "en"
    wb = Workbook()
    ws = wb.active
    ws.title = "الموظفين" if ar else "Employees"
    ws.sheet_view.rightToLeft = ar
    for j, c in enumerate(EMP_COLUMNS[1:], 1):     # no code: the program gives it
        _header_style(ws.cell(1, j, c[1] if ar else c[2]))
        ws.column_dimensions[get_column_letter(j)].width = 20
    ws.cell(2, 1, "مثال: محمد أحمد" if ar else "e.g. Mohamed Ahmed")
    ws.cell(2, 7, "2027-01-31")
    note = ws.cell(4, 1, "التواريخ تُكتب بالشكل 2027-01-31 أو 31/01/2027 — احذف سطر المثال قبل الاستيراد"
                   if ar else "Dates as 2027-01-31 or 31/01/2027 — delete the example row before importing")
    note.font = Font(size=9, color="666666")
    wb.save(path)


def _norm(s):
    return " ".join(str(s or "").strip().lower().replace("ـ", "").split())


def _to_iso(v):
    if v is None or v == "":
        return ""
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    from util import normalize_date      # same parser as the screens
    r = normalize_date(str(v).split(" ")[0])
    return r or ""


def read_employees(path):
    """Read employees from any Excel sheet whose header row contains
    at least a name column. Returns (rows, warnings)."""
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    lookup = {}
    for key, ar, en, _ in EMP_COLUMNS:
        for h in [ar, en] + ALIASES.get(key, []):
            lookup[_norm(h)] = key
    rows = list(ws.iter_rows(values_only=True))
    header_idx, mapping = None, {}
    for i, row in enumerate(rows[:15]):
        m = {}
        for j, v in enumerate(row):
            k = lookup.get(_norm(v))
            if k and k not in m.values():
                m[j] = k
        if "name" in m.values() and len(m) >= 2:
            header_idx, mapping = i, m
            break
    if header_idx is None:
        return [], ["لم أجد صف العناوين (لازم يكون فيه عمود اسم الموظف). / "
                    "Header row with an employee name column not found."]
    out, warnings = [], []
    for i, row in enumerate(rows[header_idx + 1:], header_idx + 2):
        rec = {}
        for j, key in mapping.items():
            v = row[j] if j < len(row) else None
            kind = next(c[3] for c in EMP_COLUMNS if c[0] == key)
            if kind in ("date", "expiry"):
                iso_v = _to_iso(v)
                if v not in (None, "") and not iso_v:
                    warnings.append(f"سطر {i}: تاريخ غير مفهوم ({v}) / row {i}: bad date")
                rec[key] = iso_v
            elif kind == "money":
                try:
                    rec[key] = float(str(v).replace(",", "")) if v not in (None, "") else 0.0
                except ValueError:
                    rec[key] = 0.0
            elif kind == "status":
                rec[key] = "left" if _norm(v) in ("غادر", "left", "terminated") else "active"
            else:
                if isinstance(v, float) and v.is_integer():
                    v = int(v)
                rec[key] = "" if v is None else str(v).strip()
        name = rec.get("name", "").strip()
        if not name or name.startswith(("مثال", "e.g.")):
            continue
        rec.pop("code", None)
        out.append(rec)
    return out, warnings


def export_rows(path, db, lang, title, headers, rows, money_cols=()):
    """Generic table export (transactions, clients, expenses ...)."""
    ar = lang != "en"
    wb = Workbook()
    ws = wb.active
    ws.title = title[:30].replace("/", "-")
    ws.sheet_view.rightToLeft = ar
    n = len(headers)
    _title_rows(ws, db, lang, title, ("بتاريخ: " if ar else "Date: ") + core.iso(core.today()), n)
    hr = 5
    for j, h in enumerate(headers, 1):
        _header_style(ws.cell(hr, j, h))
    for i, vals in enumerate(rows, 1):
        for j, v in enumerate(vals, 1):
            c = ws.cell(hr + i, j, v)
            c.border = BORDER
            if (j - 1) in money_cols or isinstance(v, float):
                c.number_format = "#,##0.00"
    if money_cols and rows:
        tr = hr + len(rows) + 1
        ws.cell(tr, 1, "الإجمالي" if ar else "Total").font = Font(bold=True)
        for mc in money_cols:
            col = get_column_letter(mc + 1)
            c = ws.cell(tr, mc + 1, f"=SUM({col}{hr + 1}:{col}{hr + len(rows)})")
            c.font = Font(bold=True)
            c.number_format = "#,##0.00"
    ws.freeze_panes = ws.cell(hr + 1, 1)
    if rows:
        ws.auto_filter.ref = f"A{hr}:{get_column_letter(n)}{hr + len(rows)}"
    _autowidth(ws, hr, n)
    wb.save(path)
