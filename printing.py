# -*- coding: utf-8 -*-
"""Quotation / invoice / receipt / letter / statement -> PDF with the company
letterhead.  PDFs are saved inside the client's folder (مستندات صادرة)."""
import html
import json
from pathlib import Path

import os
import sys
import tempfile

import core
from numwords import amount_in_words

BROWSER = False          # the web server sets True (browsers handle dir=rtl tables)
GOLD = "#A8841A"
BLACK = "#141414"


def _e(s):
    return html.escape(str(s or "")).replace("\n", "<br>")


def W(lang, ar, en):
    return en if lang == "en" else ar


def _lbl(value, lang):
    return core.split_label(value, lang)


def _money(v):
    return f"{(v or 0):,.2f}"


def letterhead(db, lang):
    """Built-in header, used only when no letterhead PDF is set."""
    logo = logo_file(db)
    logo_html = f'<img src="{logo.as_uri()}" height="75">' if logo else ""
    ar_name = _e(db.get("company_name_ar"))
    en_name = _e(db.get("company_name_en"))
    cr = _e(db.get("company_cr"))
    lic = _e(db.get("company_license"))
    sep = " &nbsp;|&nbsp; " if cr else ""
    lic_en = f"{sep}License {lic}" if lic else ""
    lic_ar = f"{sep}رخصة تجارية {lic}" if lic else ""
    contact = " &nbsp;|&nbsp; ".join(_e(x) for x in (db.get("company_phone"),
                                                     db.get("company_email")) if x)
    addr = _e(db.get("company_address"))
    return f"""
    <table width="100%" cellspacing="0" cellpadding="2" dir="ltr">
      <tr>
        <td width="38%" align="left" valign="middle">
          <span style="font-size:12pt; font-weight:bold; color:{BLACK};">{en_name}</span><br>
          <span style="font-size:8pt; color:#555;">{("C.R. " + cr) if cr else ""}{lic_en}</span>
        </td>
        <td width="24%" align="center" valign="middle">{logo_html}</td>
        <td width="38%" align="right" valign="middle" dir="rtl">
          <span style="font-size:12pt; font-weight:bold; color:{BLACK};">{ar_name}</span><br>
          <span style="font-size:8pt; color:#555;">{("سجل تجاري " + cr) if cr else ""}{lic_ar}</span>
        </td>
      </tr>
      <tr><td colspan="3" align="center" style="font-size:8pt; color:#555;">{" &nbsp;|&nbsp; ".join(x for x in (contact, addr) if x)}</td></tr>
    </table>
    <hr style="border:0; height:2px; background:{GOLD}; color:{GOLD};">
    """


def asset(name):
    """Files shipped with the program (letterhead, logo)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    p = base / "assets" / name
    return p if p.exists() else None


def logo_file(db):
    custom = db.get("logo_path")
    if custom and Path(custom).exists():
        return Path(custom)
    if db.get("builtin_brand") == "0":      # another company: no Al Manadeeb logo
        return None
    return asset("logo_full.png")


def letterhead_file(db):
    """The letterhead PDF to print on, or None when switched off / missing."""
    if db.get("use_letterhead") == "0":
        return None
    custom = db.get("letterhead_pdf")
    if custom and Path(custom).exists():
        return Path(custom)
    if db.get("builtin_brand") == "0":      # another company: printed header with its own name
        return None
    return asset("letterhead.pdf")


def _mm(db, key, default):
    try:
        return float(db.get(key) or default)
    except ValueError:
        return default


def _wrap(db, lang, body):
    d = "ltr" if lang == "en" else "rtl"
    align = "left" if lang == "en" else "right"
    head = "" if letterhead_file(db) else letterhead(db, lang)
    return f"""<html><body dir="{d}" style="font-family:'Segoe UI','Arial'; font-size:10pt;">
    {head}
    <div dir="{d}" align="{align}">{body}</div>
    </body></html>"""


def _row(cells, lang):
    """Qt ignores dir=rtl on tables, so Arabic rows are written reversed (not in a browser)."""
    if BROWSER:
        return "<tr>" + "".join(cells) + "</tr>"
    return "<tr>" + "".join(reversed(cells) if lang != "en" else cells) + "</tr>"


def _title(text):
    return (f'<p align="center" style="font-size:16pt; font-weight:bold; color:{BLACK};">'
            f'{_e(text)}</p>')


def _kv_table(lang, pairs):
    d = "ltr" if lang == "en" else "rtl"
    al = "left" if lang == "en" else "right"
    rows = "".join(_row([
        f'<td align="{al}" style="color:#666; padding:3px 8px;" width="24%">{_e(k)}</td>',
        f'<td align="{al}" style="padding:3px 8px; font-weight:bold;">{_e(v)}</td>'], lang)
        for k, v in pairs if v not in (None, ""))
    return f'<table dir="{d}" width="100%" cellspacing="0" cellpadding="2">{rows}</table>'


def _items_table(lang, items, gov_fees, discount, extra_rows=(), db=None):
    d = "ltr" if lang == "en" else "rtl"
    head = [W(lang, "م", "#"), W(lang, "البيان", "Description"), W(lang, "الكمية", "Qty"),
            W(lang, "السعر", "Unit price"), W(lang, "الإجمالي", "Total")]
    th = _row([f'<th style="background:{BLACK}; color:#E6C65C; padding:6px;">{h}</th>'
                for h in head], lang)
    al = "left" if lang == "en" else "right"
    body = ""
    for i, it in enumerate(items, 1):
        qty = float(it.get("qty", 1) or 0)
        price = float(it.get("price", 0) or 0)
        qty_s = f"{qty:g}"
        body += _row([f'<td align="center" width="6%">{i}</td>',
                       f'<td align="{al}">{_e(it.get("desc", ""))}</td>',
                       f'<td align="center" width="10%">{qty_s}</td>',
                       f'<td align="center" width="16%">{_money(price)}</td>',
                       f'<td align="center" width="16%">{_money(qty * price)}</td>'], lang)
    sub = core.items_total(items)
    total = sub + float(gov_fees or 0) - float(discount or 0)
    summary = [(W(lang, "مجموع الخدمات", "Services subtotal"), sub)]
    if gov_fees:
        summary.append((W(lang, "رسوم حكومية", "Government fees"), gov_fees))
    if discount:
        summary.append((W(lang, "خصم", "Discount"), -float(discount)))
    summary.append((W(lang, f"الإجمالي المستحق ({db.cur_sym('ar') if db else 'ر.ق'})",
                       f"Grand total ({db.cur_sym('en') if db else 'QAR'})"), total))
    summary += list(extra_rows)
    foot = ""
    for k, v in summary:
        bold = "font-weight:bold;" if k in (summary[-1 - len(extra_rows)][0],) else ""
        foot += _row([f'<td colspan="4" align="{al}" style="padding:4px; {bold}">{_e(k)}</td>',
                      f'<td align="center" style="padding:4px; {bold}">{_money(v)}</td>'], lang)
    return (f'<table dir="{d}" width="100%" border="1" cellspacing="0" cellpadding="5" '
            f'style="border-color:#BBB; border-collapse:collapse;">'
            f'{th}{body}{foot}</table>'), total


def _signature(lang):
    return (f'<br><br><table width="100%"><tr>'
            f'<td align="center" width="50%">{W(lang, "توقيع وختم الشركة", "Company stamp & signature")}'
            f'<br><br>........................</td>'
            f'<td align="center" width="50%">{W(lang, "توقيع العميل", "Client signature")}'
            f'<br><br>........................</td></tr></table>')


def _bank_block(db, lang):
    bank, iban = db.get("company_bank"), db.get("company_iban")
    if not (bank or iban):
        return ""
    return ('<p style="font-size:9pt; color:#444;">' + W(lang, "بيانات التحويل البنكي: ", "Bank details: ")
            + _e(bank) + (" &nbsp; IBAN: " + _e(iban) if iban else "") + "</p>")


# ----------------------------------------------------------------------
def quotation_html(db, qid, lang):
    q = db.quotation(qid)
    c = db.client(q["client_id"])
    items = json.loads(q["items"] or "[]")
    valid_until = ""
    d = core.parse_date(q["date"])
    if d and q["valid_days"]:
        import datetime as dt
        valid_until = (d + dt.timedelta(days=int(q["valid_days"]))).isoformat()
    table, _ = _items_table(lang, items, q["gov_fees"], q["discount"], db=db)
    body = _title(W(lang, "عرض سعر", "QUOTATION"))
    body += _kv_table(lang, [
        (W(lang, "رقم العرض", "Quotation No."), q["code"]),
        (W(lang, "التاريخ", "Date"), q["date"]),
        (W(lang, "صالح حتى", "Valid until"), valid_until),
        (W(lang, "العميل", "Client"), f"{c['name']}  ({c['code']})"),
        (W(lang, "السجل التجاري", "C.R."), c["cr_number"]),
        (W(lang, "الهاتف", "Phone"), c["phone"]),
    ])
    body += "<br>" + table
    if q["terms"]:
        body += f'<p><b>{W(lang, "الشروط", "Terms")}:</b><br>{_e(q["terms"])}</p>'
    if q["notes"]:
        body += f'<p><b>{W(lang, "ملاحظات", "Notes")}:</b><br>{_e(q["notes"])}</p>'
    body += _bank_block(db, lang) + _signature(lang)
    return _wrap(db, lang, body), c, f"{q['code']}"


def invoice_html(db, iid, lang):
    i = db.invoice(iid)
    c = db.client(i["client_id"])
    items = json.loads(i["items"] or "[]")
    paid = db.invoice_paid(iid)
    total = core.doc_total(i)
    extra = [(W(lang, "المدفوع", "Paid"), paid),
             (W(lang, "المتبقي", "Balance due"), total - paid)]
    table, _ = _items_table(lang, items, i["gov_fees"], i["discount"], extra, db=db)
    tx = db.transaction(i["tx_id"]) if i["tx_id"] else None
    body = _title(W(lang, "فاتورة", "INVOICE"))
    if i["status"] == "cancelled":
        body += f'<p align="center" style="color:#C62828; font-size:14pt;">{W(lang, "ملغاة", "CANCELLED")}</p>'
    body += _kv_table(lang, [
        (W(lang, "رقم الفاتورة", "Invoice No."), i["code"]),
        (W(lang, "التاريخ", "Date"), i["date"]),
        (W(lang, "تاريخ الاستحقاق", "Due date"), i["due_date"]),
        (W(lang, "العميل", "Client"), f"{c['name']}  ({c['code']})"),
        (W(lang, "السجل التجاري", "C.R."), c["cr_number"]),
        (W(lang, "رقم المعاملة", "Transaction"), tx["code"] if tx else ""),
    ])
    body += "<br>" + table
    if i["notes"]:
        body += f'<p>{_e(i["notes"])}</p>'
    body += _bank_block(db, lang) + _signature(lang)
    return _wrap(db, lang, body), c, f"{i['code']}"


def receipt_html(db, pid, lang):
    p = db.payment(pid)
    c = db.client(p["client_id"]) if p["client_id"] else None
    body = _title(W(lang, "سند قبض", "RECEIPT VOUCHER"))
    body += _kv_table(lang, [
        (W(lang, "رقم السند", "Receipt No."), p["code"]),
        (W(lang, "التاريخ", "Date"), p["pay_date"]),
        (W(lang, "استلمنا من", "Received from"),
         f"{c['name']}  ({c['code']})" if c else ""),
        (W(lang, "مبلغ وقدره", "Amount"), f"{_money(p['amount'])} " + db.cur_name(lang)),
        (W(lang, "المبلغ بالحروف", "In words"), amount_in_words(p["amount"], lang, db.currency())),
        (W(lang, "طريقة الدفع", "Payment method"), _lbl(p["method"], lang)),
        (W(lang, "وذلك عن", "For"), " / ".join(x for x in (
            f"{p['tx_code']} - {_lbl(p['tx_type'], lang)}" if p["tx_code"] else "",
            (W(lang, "فاتورة ", "Invoice ") + p["invoice_code"]) if p["invoice_code"] else "",
            p["note"]) if x)),
    ])
    body += _signature(lang).replace(W(lang, "توقيع العميل", "Client signature"),
                                     W(lang, "المستلم", "Received by"))
    return _wrap(db, lang, body), c, f"{p['code']}"


def voucher_html(db, vid, lang):
    v = db.voucher(vid)
    c = db.client(v["client_id"]) if v["client_id"] else None
    to = f"{c['name']}  ({c['code']})" if c else (v["payee"] or "")
    if c and v["payee"]:
        to += f" - {v['payee']}"
    if v["kind"] == "refund":
        acc = W(lang, "رد مبلغ للعميل", "Refund to client")
    else:
        acc = db.account_label(v["account_id"], lang) if v["account_id"] else ""
    incoming = v["kind"] == "receipt"
    body = _title(W(lang, "سند قبض", "RECEIPT VOUCHER") if incoming else W(lang, "سند صرف", "PAYMENT VOUCHER"))
    body += _kv_table(lang, [
        (W(lang, "رقم السند", "Voucher No."), v["code"]),
        (W(lang, "التاريخ", "Date"), v["date"]),
        (W(lang, "استلمنا من", "Received from") if incoming else W(lang, "صرفنا إلى", "Paid to"), to),
        (W(lang, "مبلغ وقدره", "Amount"), f"{_money(v['amount'])} " + db.cur_name(lang)),
        (W(lang, "المبلغ بالحروف", "In words"), amount_in_words(v["amount"], lang, db.currency())),
        (W(lang, "طريقة الدفع", "Payment method"), _lbl(v["method"], lang)),
        (W(lang, "البند", "Account"), acc),
        (W(lang, "وذلك عن", "For"), v["note"]),
    ])
    body += _signature(lang).replace(W(lang, "توقيع العميل", "Client signature"),
                                     W(lang, "توقيع الدافع", "Payer signature") if incoming
                                     else W(lang, "توقيع المستلم", "Receiver signature"))
    return _wrap(db, lang, body), c, f"{v['code']}"


def letter_html(db, text, lang, title="", client=None):
    import datetime as dt
    body = f'<p align="{"right" if lang != "en" else "left"}" style="color:#555;">' \
           f'{W(lang, "التاريخ: ", "Date: ")}{dt.date.today().isoformat()}</p>'
    if title:
        body += _title(title)
    body += f'<p style="font-size:11pt; line-height:160%;">{_e(text)}</p>'
    return _wrap(db, lang, body), client, safe_title(title or "letter")


def statement_html(db, cid, lang):
    c = db.client(cid)
    lines = db.client_statement(cid)
    d = "ltr" if lang == "en" else "rtl"
    head = [W(lang, "التاريخ", "Date"), W(lang, "المرجع", "Ref."), W(lang, "البيان", "Description"),
            W(lang, "مدين (عليه)", "Debit"), W(lang, "دائن (دفع)", "Credit"), W(lang, "الرصيد", "Balance")]
    th = _row([f'<th style="background:{BLACK}; color:#E6C65C; padding:5px;">{h}</th>'
               for h in head], lang)
    rows = "".join(_row([
        f'<td align="center">{_e(ln["date"])}</td>', f'<td align="center">{_e(ln["ref"])}</td>',
        f'<td>{_e(_lbl(ln["desc"], lang))}</td>',
        f'<td align="center">{_money(ln["debit"]) if ln["debit"] else ""}</td>',
        f'<td align="center">{_money(ln["credit"]) if ln["credit"] else ""}</td>',
        f'<td align="center">{_money(ln["balance"])}</td>'], lang) for ln in lines)
    bal = lines[-1]["balance"] if lines else 0
    body = _title(W(lang, "كشف حساب عميل", "STATEMENT OF ACCOUNT"))
    body += _kv_table(lang, [(W(lang, "العميل", "Client"), f"{c['name']}  ({c['code']})"),
                             (W(lang, "السجل التجاري", "C.R."), c["cr_number"]),
                             (W(lang, "حتى تاريخ", "As of"), core.iso(core.today()))])
    body += (f'<br><table dir="{d}" width="100%" border="1" cellspacing="0" cellpadding="4" '
             f'style="border-color:#BBB;">{th}{rows}</table>')
    body += (f'<p style="font-size:12pt; font-weight:bold;">'
             f'{W(lang, "الرصيد المستحق: ", "Balance due: ")}{_money(bal)} {db.cur_sym(lang)}</p>')
    return _wrap(db, lang, body), c, f"كشف حساب {core.iso(core.today())}"


def safe_title(s):
    return core.safe_name(s, 50)


# ----------------------------------------------------------------------
def _free_target(path):
    """If the PDF is open in a viewer (locked), write next to it with a new name."""
    path = Path(path)
    if not path.exists():
        return path
    try:
        with open(path, "ab"):
            return path
    except OSError:
        n = 2
        while True:
            alt = path.with_name(f"{path.stem} ({n}){path.suffix}")
            if not alt.exists():
                return alt
            n += 1


def _paint(html_text, path, lang, margins_mm):
    """Draw the html on A4 pages with the given margins (no page numbers,
    transparent background so it can sit on the letterhead)."""
    from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt
    from PySide6.QtGui import (QPageLayout, QPageSize, QPainter, QPdfWriter, QTextDocument,
                               QTextOption)
    writer = QPdfWriter(str(path))
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4),
                                     QPageLayout.Orientation.Portrait,
                                     QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter))
    writer.setResolution(300)
    writer.setTitle(Path(path).stem)
    left, top, right, bottom = margins_mm
    px = 300 / 25.4
    page_w, page_h = 210 * px, 297 * px
    cw, ch = page_w - (left + right) * px, page_h - (top + bottom) * px
    doc = QTextDocument()
    doc.documentLayout().setPaintDevice(writer)
    opt = QTextOption()
    opt.setTextDirection(Qt.LayoutDirection.LeftToRight if lang == "en"
                         else Qt.LayoutDirection.RightToLeft)
    doc.setDefaultTextOption(opt)
    doc.setHtml(html_text)
    doc.setPageSize(QSizeF(cw, ch))
    pages = max(1, doc.pageCount())
    painter = QPainter(writer)
    try:
        for i in range(pages):
            if i:
                writer.newPage()
            painter.save()
            painter.translate(left * px, top * px - i * ch)
            doc.drawContents(painter, QRectF(0, i * ch, cw, ch))
            painter.restore()
    finally:
        painter.end()
    return pages


def render_pdf(html_text, path, lang="ar", db=None):
    path = _free_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lh = letterhead_file(db) if db is not None else None
    if not lh:
        _paint(html_text, path, lang, (14, 12, 14, 14))
        return path
    margins = (_mm(db, "lh_side_mm", 18), _mm(db, "lh_top_mm", 46),
               _mm(db, "lh_side_mm", 18), _mm(db, "lh_bottom_mm", 30))
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        _paint(html_text, tmp, lang, margins)
        from pypdf import PageObject, PdfReader, PdfWriter
        paper = PdfReader(str(lh)).pages[0]
        content = PdfReader(tmp)
        out = PdfWriter()
        w, h = float(paper.mediabox.width), float(paper.mediabox.height)
        for cp in content.pages:
            page = PageObject.create_blank_page(width=w, height=h)
            page.merge_page(paper)
            page.merge_page(cp)
            out.add_page(page)
        out.add_metadata({"/Title": path.stem})
        with open(path, "wb") as f:
            out.write(f)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return path


def save_pdf(db, kind, ref, lang, text=None, title=None, client_id=None):
    """kind: quotation | invoice | receipt | letter | statement.
    Returns the path of the PDF (in the client's 'issued documents' folder)."""
    if kind == "quotation":
        h, c, name = quotation_html(db, ref, lang)
    elif kind == "invoice":
        h, c, name = invoice_html(db, ref, lang)
    elif kind == "receipt":
        h, c, name = receipt_html(db, ref, lang)
    elif kind == "voucher":
        h, c, name = voucher_html(db, ref, lang)
    elif kind == "statement":
        h, c, name = statement_html(db, ref, lang)
    elif kind == "sale_invoice":
        h, c, name = sales_invoice_html(db, ref, lang)
    elif kind == "sale_receipt":
        h, c, name = sales_receipt_html(db, ref, lang)
    elif kind == "supplier_payment":
        h, c, name = supplier_payment_html(db, ref, lang)
    else:
        c = db.client(client_id) if client_id else None
        h, c, name = letter_html(db, text or "", lang, title or "", c)
    if c:
        folder = db.client_folder(c["id"]) / "مستندات صادرة - Issued"
    else:
        folder = db.files_root() / "مستندات صادرة - Issued"
    suffix = "" if lang != "en" else " (EN)"
    target = folder / f"{core.safe_name(name, 60)}{suffix}.pdf"
    n = 2
    while target.exists() and kind == "letter":
        target = folder / f"{core.safe_name(name, 60)}{suffix} ({n}).pdf"
        n += 1
    return render_pdf(h, target, lang, db)


def report_pdf(db, lang, title, subtitle, headers, rows, bold_rows=()):
    """Generic table report (trial balance, ledger, P&L...) -> PDF in the reports folder."""
    th = _row([f'<th style="background:{BLACK}; color:#E6C65C; padding:5px;">{_e(h)}</th>'
               for h in headers], lang)
    body_rows = ""
    for i, vals in enumerate(rows):
        style = "font-weight:bold; background:#F2EBD3;" if i in bold_rows else ""
        cells = []
        for v in vals:
            if isinstance(v, float):
                cells.append(f'<td align="center" style="{style}">{_money(v) if v else ""}</td>')
            else:
                cells.append(f'<td style="{style}">{_e(v)}</td>')
        body_rows += _row(cells, lang)
    body = _title(title)
    if subtitle:
        body += f'<p align="center" style="color:#555;">{_e(subtitle)}</p>'
    body += (f'<table width="100%" border="1" cellspacing="0" cellpadding="4" '
             f'style="border-color:#BBB; font-size:9pt;">{th}{body_rows}</table>')
    html_text = _wrap(db, lang, body)
    folder = db.files_root() / "تقارير الحسابات - Accounting Reports"
    import datetime as dt
    target = folder / f"{core.safe_name(title, 50)} {dt.datetime.now():%Y-%m-%d_%H%M}.pdf"
    return render_pdf(html_text, target, lang, db)


# ----------------------------------------------------------------------
# client companies (trading): sales invoice, receipt, supplier payment
# ----------------------------------------------------------------------
def _lines_table(db, lang, lines, discount, vat_rate):
    from business import doc_totals
    d = "ltr" if lang == "en" else "rtl"
    al = "left" if lang == "en" else "right"
    head = [W(lang, "م", "#"), W(lang, "الصنف / البيان", "Item / description"), W(lang, "الكمية", "Qty"),
            W(lang, "السعر", "Unit price"), W(lang, "الإجمالي", "Amount")]
    th = _row([f'<th style="background:{BLACK}; color:#E6C65C; padding:5px;">{_e(h)}</th>' for h in head], lang)
    body = ""
    for n, l in enumerate(lines, 1):
        qty, price = float(l.get("qty") or 0), float(l.get("price") or 0)
        desc = l.get("desc", "")
        if l.get("unit"):
            desc += f" ({l['unit']})"
        body += _row([f'<td align="center" width="5%">{n}</td>',
                      f'<td align="{al}">{_e(desc)}</td>',
                      f'<td align="center" width="10%">{qty:g}</td>',
                      f'<td align="center" width="16%">{_money(price)}</td>',
                      f'<td align="center" width="16%">{_money(qty * price)}</td>'], lang)
    sub, disc, net, vat, total = doc_totals(lines, discount, vat_rate)
    summary = [(W(lang, "الإجمالي قبل الخصم", "Subtotal"), sub)]
    if disc:
        summary += [(W(lang, "خصم", "Discount"), -disc), (W(lang, "الصافي قبل الضريبة", "Net before VAT"), net)]
    if float(vat_rate or 0):
        summary.append((W(lang, f"ضريبة القيمة المضافة {float(vat_rate):g}%", f"VAT {float(vat_rate):g}%"), vat))
    summary.append((W(lang, f"الإجمالي المستحق ({db.cur_sym('ar')})", f"Total due ({db.cur_sym('en')})"), total))
    foot = ""
    for i, (k, v) in enumerate(summary):
        bold = "font-weight:bold;" if i == len(summary) - 1 else ""
        foot += _row([f'<td colspan="4" align="{al}" style="padding:4px; {bold}">{_e(k)}</td>',
                      f'<td align="center" style="padding:4px; {bold}">{_money(v)}</td>'], lang)
    return (f'<table dir="{d}" width="100%" border="1" cellspacing="0" cellpadding="5" '
            f'style="border-color:#BBB; border-collapse:collapse;">{th}{body}{foot}</table>'), total


def sales_invoice_html(db, sid, lang):
    from business import doc_lines
    inv = db.sales_invoice(sid)
    c = db.client(inv["client_id"])
    lines = doc_lines(inv)
    for l in lines:
        if l.get("item_id") and not l.get("unit"):
            it = db.item(l["item_id"])
            if it and it["unit"]:
                l["unit"] = it["unit"]
    vat_no = db.get("vat_number")
    taxed = float(inv["vat_rate"] or 0) > 0
    body = _title(W(lang, "فاتورة ضريبية", "TAX INVOICE") if taxed and vat_no else W(lang, "فاتورة", "INVOICE"))
    body += _kv_table(lang, [
        (W(lang, "رقم الفاتورة", "Invoice No."), inv["code"]),
        (W(lang, "التاريخ", "Date"), inv["date"]),
        (W(lang, "تاريخ الاستحقاق", "Due date"), inv["due_date"]),
        (W(lang, "الرقم الضريبي للبائع", "Seller VAT No."), vat_no if taxed else ""),
        (W(lang, "العميل", "Customer"), f"{c['name']}  ({c['code']})" if c else ""),
        (W(lang, "هاتف العميل", "Customer phone"), c["phone"] if c else ""),
        (W(lang, "السجل التجاري للعميل", "Customer C.R."), c["cr_number"] if c else ""),
    ])
    table, total = _lines_table(db, lang, lines, inv["discount"], inv["vat_rate"])
    body += "<br>" + table
    body += (f'<p style="font-size:9pt;">{W(lang, "المبلغ بالحروف: ", "In words: ")}'
             f'{_e(amount_in_words(total, lang, db.currency()))}</p>')
    paid = db.invoice_received(sid)
    if paid:
        body += (f'<p style="font-size:9pt;">{W(lang, "المدفوع: ", "Paid: ")}{_money(paid)} &nbsp; | &nbsp; '
                 f'{W(lang, "المتبقي: ", "Balance: ")}{_money(total - paid)} {db.cur_sym(lang)}</p>')
    if inv["notes"]:
        body += f'<p style="font-size:9pt;">{_e(inv["notes"])}</p>'
    body += _bank_block(db, lang)
    body += _signature(lang)
    return _wrap(db, lang, body), c, inv["code"]


def sales_receipt_html(db, rid, lang):
    r = db.sales_receipt(rid)
    c = db.client(r["client_id"])
    body = _title(W(lang, "سند قبض", "RECEIPT VOUCHER"))
    body += _kv_table(lang, [
        (W(lang, "رقم السند", "Receipt No."), r["code"]),
        (W(lang, "التاريخ", "Date"), r["date"]),
        (W(lang, "استلمنا من", "Received from"), f"{c['name']}  ({c['code']})" if c else ""),
        (W(lang, "مبلغ وقدره", "Amount"), f"{_money(r['amount'])} " + db.cur_name(lang)),
        (W(lang, "المبلغ بالحروف", "In words"), amount_in_words(r["amount"], lang, db.currency())),
        (W(lang, "طريقة الدفع", "Payment method"), _lbl(r["method"], lang)),
        (W(lang, "وذلك عن", "For"), " / ".join(x for x in (
            (W(lang, "فاتورة ", "Invoice ") + r["invoice_code"]) if r["invoice_code"] else "",
            r["note"]) if x)),
    ])
    body += _signature(lang).replace(W(lang, "توقيع العميل", "Client signature"),
                                     W(lang, "المستلم", "Received by"))
    return _wrap(db, lang, body), c, r["code"]


def supplier_payment_html(db, pid, lang):
    p = db.supplier_payment(pid)
    body = _title(W(lang, "سند صرف", "PAYMENT VOUCHER"))
    body += _kv_table(lang, [
        (W(lang, "رقم السند", "Voucher No."), p["code"]),
        (W(lang, "التاريخ", "Date"), p["date"]),
        (W(lang, "صرفنا إلى", "Paid to"), p["supplier_name"]),
        (W(lang, "مبلغ وقدره", "Amount"), f"{_money(p['amount'])} " + db.cur_name(lang)),
        (W(lang, "المبلغ بالحروف", "In words"), amount_in_words(p["amount"], lang, db.currency())),
        (W(lang, "طريقة الدفع", "Payment method"), _lbl(p["method"], lang)),
        (W(lang, "وذلك عن", "For"), " / ".join(x for x in (
            (W(lang, "فاتورة مشتريات ", "Purchase bill ") + p["bill_code"]) if p["bill_code"] else "",
            p["note"]) if x)),
    ])
    body += _signature(lang).replace(W(lang, "توقيع العميل", "Client signature"),
                                     W(lang, "المستلم", "Received by"))
    return _wrap(db, lang, body), None, p["code"]
