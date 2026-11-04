# -*- coding: utf-8 -*-
"""استيراد حسابات المناديب المبدعون التاريخية (يونيو 2022 - ديسمبر 2023) من ملف Excel.

Decisions taken with the owner (Ahmed):
- personal items (housing rent, tickets, umrah, family visas, iPhone, advances, loan)
  -> owner's drawings (3102), not expenses
- office furniture / AC / sign / devices bought in 2023 -> fixed assets
- the fake demo data is wiped first
- one reporting period: 2022-06-01 .. 2023-12-31
"""
import datetime as dt
import json
import re
import sys

import openpyxl

START, END = "2022-06-01", "2023-12-31"
OWNER = "أحمد شعبان"

PERSONAL = ["سكن", "شخصية", "عمرة", "تذكرة", "تاشيرة العيال", "تاشيرة ماما", "تاشيرة الامارات",
            "السعودية", "ايفون", "سلف", "قرض"]
ASSET_COMP = ["جهاز مكروسوفت", "رواتير"]
ASSET_FURN = ["الانارة", "اثاث مكتب", "التكييف", "اللوحة", "ثلاجة"]
# fix obvious typos: rows listed under December 2023 but dated January 2023
DATE_FIX = {("2023-01-02", "ايجار السكن"): "2023-12-02", ("2023-01-03", "فاتورة تلفون"): "2023-12-03"}


def pdate(v):
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, str):
        m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", v)
        if m:
            return dt.date(int(m[3]), int(m[2]), int(m[1])).isoformat()
    return None


def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def read_workbook(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    rev = []
    ws = wb["الايرارات 2023 "]
    for r in range(1, ws.max_row + 1):
        d, amt = pdate(ws.cell(r, 3).value), ws.cell(r, 7).value
        if d and isinstance(amt, (int, float)) and amt:
            rev.append({"row": r, "date": d, "service": clean(ws.cell(r, 4).value),
                        "desc": clean(ws.cell(r, 5).value), "client": clean(ws.cell(r, 6).value),
                        "amount": float(amt), "note": clean(ws.cell(r, 8).value)})
    exp = []
    ws = wb["المصاريف 2023"]
    cols = {4: "310", 5: "311", 6: "312", 7: "313", 8: "314", 9: "315", 10: "316"}
    for r in range(1, ws.max_row + 1):
        d = pdate(ws.cell(r, 3).value)
        if not d:
            continue
        note = clean(ws.cell(r, 11).value)
        for c, code in cols.items():
            v = ws.cell(r, c).value
            if isinstance(v, (int, float)) and v:
                exp.append({"row": r, "date": DATE_FIX.get((d, note), d), "orig_date": d,
                            "col": code, "amount": float(v), "note": note})
    bank = []
    ws = wb["البنك  "]
    for r in range(2, ws.max_row + 1):
        d = pdate(ws.cell(r, 8).value)
        if not d:
            continue
        bank.append({"date": d, "deposit": ws.cell(r, 5).value or 0, "open_fee": ws.cell(r, 6).value or 0,
                     "fee": ws.cell(r, 7).value or 0, "note": clean(ws.cell(r, 9).value)})
    assets = []
    ws = wb["الاصول "]
    for r in range(2, ws.max_row + 1):
        name, price = clean(ws.cell(r, 3).value), ws.cell(r, 5).value
        if name and isinstance(price, (int, float)):
            assets.append({"name": name, "detail": clean(ws.cell(r, 4).value), "price": float(price)})
    return rev, exp, bank, assets


def classify(e):
    """-> (kind, account_code, label). kind: expense | drawing | asset"""
    n, col = e["note"], e["col"]
    if any(k in n for k in ASSET_COMP):
        return "asset", "1202", "أجهزة كمبيوتر وشبكات"
    if any(k in n for k in ASSET_FURN):
        return "asset", "1201", "أثاث وتجهيزات المكتب"
    if any(k in n for k in PERSONAL) or (col == "310" and "مكتب" not in n):
        # every rent in the sheet is the owner's housing except the one office rent
        return "drawing", "3102", "مسحوبات شخصية"
    if col == "310":
        return "expense", "5102", "إيجار المكتب"
    if col == "311":
        return "expense", "5101", "رواتب وأجور"
    if col == "312":
        return "expense", "5104", "اتصالات وإنترنت"
    if col == "314":
        if "ZOHO" in n or "موقع" in n:
            return "expense", "5105", "اشتراكات وبرامج"
        if "حبر" in n:
            return "expense", "5106", "طباعة وقرطاسية"
        return "expense", "5111", "دعاية وتسويق"
    if col == "315":
        return "expense", "5107", "رسوم حكومية على الشركة"
    if col == "316":
        return "expense", "5103", "مواصلات وبنزين وسيارة"
    if "طباعة" in n:
        return "expense", "5106", "طباعة وقرطاسية"
    return "expense", "5199", "مصروفات أخرى"


def drawing_group(n):
    if "سكن" in n or not n:
        return "إيجار السكن"
    if "تذكرة" in n or "تاشيرة" in n or "السعودية" in n:
        return "سفر وتذاكر وتأشيرات"
    if "عمرة" in n:
        return "عمرة"
    if "سلف" in n or "قرض" in n:
        return "سلف وسداد قروض شخصية"
    return "مصاريف شخصية أخرى"


def plan(path):
    """Everything that will be posted, in date order (also used for the reports/checks)."""
    rev, exp, bank, assets = read_workbook(path)
    items = []
    contributed = [a for a in assets if "surfec" not in a["detail"].lower() and a["name"] != "m s s"]
    items.append({"date": START, "type": "opening", "lines": (
        [("1202", sum(a["price"] for a in contributed), 0), ("1203", 10000, 0),
         ("3101", 0, sum(a["price"] for a in contributed) + 10000)]),
        "memo": "أصول قدّمها المالك للشركة عند البداية: أجهزة ومعدات مكتب (" +
                "، ".join(a["name"] for a in contributed) + ") + سيارة الشركة",
        "amount": sum(a["price"] for a in contributed) + 10000})
    items.append({"date": "2023-03-19", "type": "capital", "amount": 20000,
                  "memo": "إيداع رأس المال من أحمد شعبان (19/3/2023)"})
    for b in bank:
        if b["deposit"]:
            items.append({"date": b["date"], "type": "bank_deposit", "amount": float(b["deposit"]),
                          "memo": "فتح حساب QNB وإيداع من الصندوق"})
        for amt, why in ((b["open_fee"], "مصاريف فتح الحساب"), (b["fee"], b["note"] or "مصاريف الحساب")):
            if amt:
                items.append({"date": b["date"], "type": "bank_fee", "amount": float(amt),
                              "memo": "رسوم بنكية QNB: " + why})
    for r in rev:
        acc = "4102" if ("تاسيس" in r["desc"] or "تأسيس" in r["desc"]) else "4101"
        items.append({"date": r["date"], "type": "revenue", "account": acc, "amount": r["amount"],
                      "memo": f"{r['desc']} — {r['client']} ({r['service']})", "client": r["client"],
                      "service": r["service"], "desc": r["desc"]})
    for e in exp:
        kind, acc, label = classify(e)
        items.append({"date": e["date"], "type": kind, "account": acc, "label": label,
                      "amount": e["amount"], "memo": e["note"] or label, "col": e["col"],
                      "group": drawing_group(e["note"]) if kind == "drawing" else ""})
    items.sort(key=lambda x: (x["date"], {"opening": 0, "capital": 1, "bank_deposit": 2}.get(x["type"], 5)))
    return items, rev, exp, bank, assets


# ------------------------------------------------------------------ program side
BUSINESS_TABLES = ["jlines", "journal", "vouchers", "payments", "tx_log", "invoices", "quotations",
                   "expenses", "tasks", "files", "documents", "owners", "employees", "transactions", "clients"]


def wipe_business_data(db):
    """Remove clients, employees, transactions, sales and accounting entries.
    Keeps users, permissions, settings, templates, chart of accounts and the activity log."""
    for t in BUSINESS_TABLES:
        db.con.execute(f"DELETE FROM {t}")
    db.con.execute("DELETE FROM settings WHERE key LIKE 'seq_%'")
    db.con.execute("DELETE FROM sqlite_sequence WHERE name IN (%s)" % ",".join("?" * len(BUSINESS_TABLES)),
                   BUSINESS_TABLES)
    db.con.commit()


def post_all(db, items):
    acc = {a["code"]: a["id"] for a in db.q("SELECT id, code FROM accounts")}
    cash, bank = acc["1101"], acc["1102"]
    db._bypass_lock = True
    n = {"entries": 0, "expenses": 0, "vouchers": 0}
    with db.atomic():
        for it in items:
            t, amt, d = it["type"], round(it["amount"], 2), it["date"]
            if t == "opening":
                db.save_manual_entry(d, it["memo"], [{"account_id": acc[c], "debit": dr, "credit": cr}
                                                     for c, dr, cr in it["lines"]])
                n["entries"] += 1
            elif t == "capital":
                db.save_manual_entry(d, it["memo"], [{"account_id": cash, "debit": amt},
                                                     {"account_id": acc["3101"], "credit": amt}])
                n["entries"] += 1
            elif t == "bank_deposit":
                db.save_manual_entry(d, it["memo"], [{"account_id": bank, "debit": amt},
                                                     {"account_id": cash, "credit": amt}])
                n["entries"] += 1
            elif t == "bank_fee":
                db.save_expense({"date": d, "description": it["memo"], "amount": amt, "method": "بنك",
                                 "account_id": acc["5109"], "paid_from_id": bank, "note": "مستورد من ملف 2022-2023"})
                n["expenses"] += 1
            elif t == "revenue":
                db.save_manual_entry(d, "إيراد: " + it["memo"],
                                     [{"account_id": cash, "debit": amt, "memo": it["client"]},
                                      {"account_id": acc[it["account"]], "credit": amt, "memo": it["desc"]}])
                n["entries"] += 1
            elif t == "expense":
                db.save_expense({"date": d, "description": it["memo"], "amount": amt, "method": "نقدي",
                                 "account_id": acc[it["account"]], "paid_from_id": cash,
                                 "note": "مستورد من ملف 2022-2023"})
                n["expenses"] += 1
            elif t in ("drawing", "asset"):
                db.save_voucher({"date": d, "kind": "payment", "account_id": acc[it["account"]],
                                 "paid_from_id": cash, "amount": amt, "method": "نقدي",
                                 "payee": f"{OWNER} (مسحوبات شخصية)" if t == "drawing" else "مورد (شراء أصل)",
                                 "note": it["memo"] + (f" — {it['group']}" if it.get("group") else "")})
                n["vouchers"] += 1
    db._bypass_lock = False
    db.set("history_imported", f"{START}..{END}")
    return n


if __name__ == "__main__":
    items = plan(sys.argv[1])[0]
    print(json.dumps({"items": len(items)}, ensure_ascii=False))
