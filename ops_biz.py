# -*- coding: utf-8 -*-
"""Server-side helpers for client (business) companies: lists with totals already worked out,
so the browser does one call per screen instead of one per row."""
import core
import users as users_mod
from business import ADJ_REASONS, DOC_TITLES, doc_lines, doc_totals


def _need(db, *perms):
    if not any(db.can(p) for p in perms):
        raise users_mod.NotAllowed(perms[0])


def _r(row):
    return dict(row) if row is not None else None


def op_biz_consts(ctx, server):
    db = ctx.db
    return {"adj_reasons": ADJ_REASONS, "doc_titles": DOC_TITLES, "vat_rate": db.vat_rate(),
            "alert_days": db.get_int("alert_days", 30), "is_business": db.is_business}


def _inv_row(db, s):
    sub, disc, net, vat, total = db.invoice_totals(s)
    got = db.invoice_received(s["id"])
    d = _r(s)
    d.update(sub_amt=sub, disc_amt=disc, net_amt=net, vat_amt=vat, total_amt=total, got_amt=got,
             rem_amt=core_r2(total - got))
    return d


def _bill_row(db, b):
    sub, disc, net, vat, total = db.bill_totals(b)
    paid = db.bill_paid(b["id"])
    d = _r(b)
    d.update(sub_amt=sub, disc_amt=disc, net_amt=net, vat_amt=vat, total_amt=total, got_amt=paid,
             rem_amt=core_r2(total - paid))
    return d


def core_r2(v):
    return round((v or 0) + 0.0, 2)


def op_biz_dashboard(ctx, server):
    db = ctx.db
    today = core.today()
    first = core.iso(today.replace(day=1))
    out = {"today": core.iso(today)}
    m = db.can("money")
    if m:
        pt = db.period_totals(first, core.iso(today))
        out["sales_month"], out["purchases_month"] = pt["sales"], pt["purchases"]
        out["ar_total"] = core_r2(sum(db.ar_balances().values()))
        out["ap_total"] = core_r2(sum(db.ap_balances().values()))
        out["cash_total"] = db.money_balance()
    if db.can("sales_view") or db.can("receipts"):
        out["unpaid_invoices"] = [dict(_r(u["inv"]), total_amt=u["total"], rem_amt=u["remaining"])
                                  for u in db.unpaid_sales_invoices()]
    if db.can("purchases"):
        out["unpaid_bills"] = [dict(_r(u["bill"]), total_amt=u["total"], rem_amt=u["remaining"])
                               for u in db.unpaid_bills()]
    out["low_stock"] = [{"id": v["item"]["id"], "name": v["item"]["name"], "unit": v["item"]["unit"],
                         "qty": v["qty"], "reorder_level": v["item"]["reorder_level"]} for v in db.low_stock()]
    out["docs"] = [_r(d) for d in db.expiring_company_docs()]
    out["alert_days"] = db.get_int("alert_days", 30)
    return out


def op_biz_invoices(ctx, server, q="", date_from=None, date_to=None, client_id=None):
    db = ctx.db
    _need(db, "sales_view")
    return [_inv_row(db, s) for s in db.sales_invoices(q or "", date_from, date_to, client_id)]


def op_biz_invoice(ctx, server, sid):
    db = ctx.db
    _need(db, "sales_view")
    s = db.sales_invoice(sid)
    if not s:
        raise ValueError("not found")
    d = _inv_row(db, s)
    d["lines"] = doc_lines(s)
    return d


def op_biz_bills(ctx, server, q="", date_from=None, date_to=None, supplier_id=None):
    db = ctx.db
    _need(db, "purchases")
    return [_bill_row(db, b) for b in db.purchase_bills(q or "", date_from, date_to, supplier_id)]


def op_biz_bill(ctx, server, bid):
    db = ctx.db
    _need(db, "purchases")
    b = db.purchase_bill(bid)
    if not b:
        raise ValueError("not found")
    d = _bill_row(db, b)
    d["lines"] = doc_lines(b)
    return d


def op_biz_open_invoices(ctx, server, client_id, select=None):
    """Invoices of one customer that still have a balance (+ the selected one)."""
    db = ctx.db
    _need(db, "sales_view", "receipts")
    out = []
    for s in db.sales_invoices(client_id=client_id):
        r = _inv_row(db, s)
        if r["rem_amt"] > 0.009 or s["id"] == select:
            out.append({"id": s["id"], "code": s["code"], "date": s["date"], "rem_amt": r["rem_amt"]})
    return out


def op_biz_open_bills(ctx, server, supplier_id, select=None):
    db = ctx.db
    _need(db, "purchases")
    out = []
    for b in db.purchase_bills(supplier_id=supplier_id):
        r = _bill_row(db, b)
        if r["rem_amt"] > 0.009 or b["id"] == select:
            out.append({"id": b["id"], "code": b["code"], "date": b["date"], "rem_amt": r["rem_amt"]})
    return out


def op_biz_items(ctx, server, q="", active_only=False):
    """Items with stock on hand, average cost and stock value."""
    db = ctx.db
    _need(db, "inventory", "sales_view", "purchases")
    val = {v["item"]["id"]: v for v in db.inventory_valuation()}
    out = []
    for it in db.items(q or "", active_only):
        d = _r(it)
        v = val.get(it["id"])
        d["qty"] = v["qty"] if v else None
        d["avg_cost"] = v["avg"] if v else None
        d["stock_value"] = v["value"] if v else None
        d["low"] = bool(v and v["low"])
        out.append(d)
    return out


def op_biz_item_card(ctx, server, item_id):
    db = ctx.db
    _need(db, "inventory")
    return db.item_card(item_id)


def op_biz_suppliers(ctx, server, q=""):
    db = ctx.db
    _need(db, "purchases")
    bal = db.ap_balances()
    return [dict(_r(s), balance_amt=bal.get(s["id"], 0.0)) for s in db.suppliers(q or "")]


def op_biz_doc_totals(ctx, server, lines, discount=0, vat_rate=0):
    sub, disc, net, vat, total = doc_totals(lines or [], discount, vat_rate)
    return {"sub": sub, "disc": disc, "net": net, "vat": vat, "total": total}


def op_biz_report(ctx, server, kind, date_from, date_to):
    db = ctx.db
    f, t = date_from or "0000-01-01", date_to or "9999-12-31"
    if kind == "vat":
        _need(db, "accounting")
        return db.vat_report(f, t)
    if kind == "items":
        _need(db, "accounting", "sales_view")
        return [{"name": a["name"], "qty": a["qty"], "amount": a["value"]} for a in db.sales_by_item(f, t)]
    if kind == "stock":
        _need(db, "inventory", "accounting")
        return [{"id": v["item"]["id"], "name": v["item"]["name"], "unit": v["item"]["unit"], "qty": v["qty"],
                 "avg_cost": v["avg"], "stock_value": v["value"], "low": bool(v["low"])}
                for v in db.inventory_valuation(t)]
    if kind == "ar":
        _need(db, "accounting", "finance")
        bal = db.ar_balances()
        return [{"id": c["id"], "code": c["code"], "name": c["name"], "phone": c["phone"],
                 "balance_amt": bal.get(c["id"], 0.0)}
                for c in db.q("SELECT id, code, name, phone FROM clients ORDER BY name")
                if abs(bal.get(c["id"], 0.0)) > 0.009]
    if kind == "ap":
        _need(db, "accounting", "purchases")
        bal = db.ap_balances()
        return [{"id": s["id"], "code": s["code"], "name": s["name"], "phone": s["phone"],
                 "balance_amt": bal.get(s["id"], 0.0)}
                for s in db.suppliers() if abs(bal.get(s["id"], 0.0)) > 0.009]
    raise ValueError("kind")


BIZ_OPS = {k: v for k, v in globals().items() if k.startswith("op_")}
