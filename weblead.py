# -*- coding: utf-8 -*-
"""استقبال العملاء المحتملين من الإنترنت (إعلانات فيسبوك وانستغرام، فورم الموقع، Google Forms).

The leads land in a Google Sheet on the owner's Google Drive (Meta lead ads → Make / Zapier → sheet,
or a Google Form's responses sheet).  A tiny Google Apps Script published as a web app hands the
rows to the program when it is asked with the secret key.  The program asks every few minutes and
adds the new people to the CRM without duplicating anyone.
"""
import hashlib
import json
import re
import secrets
import ssl
import urllib.error
import urllib.parse
import urllib.request

APPS_SCRIPT = r"""// استقبال العملاء المحتملين من الشيت
// Creative Reps — hands the sheet rows to the program (only with the secret key)
const KEY = '__KEY__';
const MAX_ROWS = 300;   // آخر 300 صف من كل ورقة

function doGet(e) {
  const p = (e && e.parameter) || {};
  if (p.key !== KEY) {
    return out({ ok: false, error: 'bad key' });
  }
  const book = SpreadsheetApp.getActiveSpreadsheet();
  const sheets = [];
  book.getSheets().forEach(function (sh) {
    const values = sh.getDataRange().getDisplayValues();
    if (values.length < 2) return;
    const start = Math.max(1, values.length - MAX_ROWS);
    sheets.push({ name: sh.getName(), id: sh.getSheetId(), headers: values[0],
                  first_row: start + 1, rows: values.slice(start) });
  });
  return out({ ok: true, book: book.getName(), sheets: sheets });
}

function out(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
"""


class WebLeadError(Exception):
    pass


def new_key():
    return secrets.token_urlsafe(18)


def script_for(key):
    return APPS_SCRIPT.replace("__KEY__", key)


def fetch(url, key, timeout=30, opener=None):
    """GET the web app -> parsed JSON. Raises WebLeadError with an Arabic message."""
    if not url or not url.startswith("https://"):
        raise WebLeadError("رابط الـ Google Script مش مكتوب أو مش صحيح (لازم يبدأ بـ https://).")
    full = url + ("&" if "?" in url else "?") + urllib.parse.urlencode({"key": key})
    try:
        if opener:
            raw = opener(full)
        else:
            req = urllib.request.Request(full, headers={"User-Agent": "CreativeReps"})
            with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
                raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise WebLeadError(f"Google رفض الطلب ({e.code}). اتأكد إن النشر متعمل لـ «Anyone».") from e
    except urllib.error.URLError as e:
        raise WebLeadError("مفيش اتصال بالإنترنت أو الرابط مش شغال.") from e
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise WebLeadError("الرابط رجّع صفحة مش بيانات. غالبًا محتاج تعمل Deploy جديد أو تدّي صلاحية «Anyone».") from e
    if not data.get("ok"):
        raise WebLeadError("المفتاح السري في الـ Script مش زي اللي في البرنامج. انسخ الكود تاني من الإعدادات."
                           if data.get("error") == "bad key" else f"خطأ من الـ Script: {data.get('error')}")
    return data


# ---------------------------------------------------------------- column mapping
def _norm(h):
    return re.sub(r"[\s_\-:]+", " ", str(h or "")).strip().lower()


NAME_KEYS = ("full name", "name", "الاسم", "الاسم بالكامل", "اسمك", "الاسم الكامل", "customer name", "اسم العميل")
FIRST_KEYS = ("first name", "الاسم الأول")
LAST_KEYS = ("last name", "اسم العائلة")
PHONE_KEYS = ("phone number", "phone", "mobile", "mobile number", "whatsapp", "رقم الهاتف", "الهاتف", "رقم الجوال",
              "الجوال", "رقم الواتساب", "واتساب", "رقم التليفون", "تليفون", "رقم الموبايل", "موبايل")
EMAIL_KEYS = ("email", "e mail", "email address", "البريد الإلكتروني", "الإيميل", "الايميل")
SERVICE_HINTS = ("service", "خدمة", "الخدمة", "المطلوب", "طلبك", "what do you need", "interested")
COMPANY_KEYS = ("company name", "company", "اسم الشركة", "الشركة", "النشاط")
PLATFORM_KEYS = ("platform", "المنصة", "source", "المصدر")
SKIP = ("id", "ad id", "adset id", "campaign id", "form id", "lead id", "is organic", "lead status",
        "timestamp", "created time", "الطابع الزمني", "date", "time")


def _pick(row, keys):
    for k in keys:
        if k in row and row[k].strip():
            return row[k].strip()
    return ""


def map_row(sheet_name, headers, values, services=(), sources=()):
    """One sheet row -> (lead data, message, ext_id) or None if it has no phone."""
    row = {}
    shown = []
    for h, v in zip(headers, values):
        k = _norm(h)
        v = str(v or "").strip()
        if not k:
            continue
        row[k] = v
        if v:
            shown.append((h.strip(), v))
    phone = _pick(row, PHONE_KEYS)
    phone = re.sub(r"^p:\s*", "", phone)                   # Meta exports phones as "p:+974..."
    if not re.search(r"\d{7,}", re.sub(r"\D", "", phone)):
        return None
    name = _pick(row, NAME_KEYS) or " ".join(x for x in (_pick(row, FIRST_KEYS), _pick(row, LAST_KEYS)) if x)
    email = _pick(row, EMAIL_KEYS)
    company = _pick(row, COMPANY_KEYS)
    service_text = ""
    for k, v in row.items():
        if v and any(hint in k for hint in SERVICE_HINTS):
            service_text = v
            break
    service = ""
    if service_text:
        for s in services:
            if any(part and (part in service_text or service_text in part) for part in s.split("|")):
                service = s
                break
        service = service or service_text
    platform = _pick(row, PLATFORM_KEYS).lower()
    sn = (sheet_name or "").lower()

    def src(ar_label):
        return next((s for s in sources if s.split("|")[0] == ar_label), ar_label)
    if platform in ("ig", "instagram") or "insta" in sn or "انستغرام" in sn:
        source = src("إعلانات انستغرام")
    elif (platform in ("fb", "facebook") or "face" in sn or "فيس" in sn or "lead" in sn or
          any(k in row for k in ("ad name", "form name", "campaign name", "adset name"))):
        source = src("إعلانات فيسبوك")
    else:
        source = src("فورم الموقع")
    used = set(PHONE_KEYS + NAME_KEYS + EMAIL_KEYS + FIRST_KEYS + LAST_KEYS + COMPANY_KEYS + PLATFORM_KEYS)
    extra = [f"{h}: {v}" for h, v in shown if _norm(h) not in used and _norm(h) not in SKIP]
    message = f"من {source.split('|')[0]} ({sheet_name})" + (" — " + " | ".join(extra) if extra else "")
    ext_id = "web:" + hashlib.sha1(("|".join([sheet_name] + [str(v) for v in values])).encode("utf-8")).hexdigest()
    data = {"name": name, "phone": phone, "email": email, "company": company, "service": service,
            "source": source}
    return data, message, ext_id


def import_payload(db, payload):
    """Add the rows of a fetch() result to the CRM. -> summary dict."""
    services = db.get_list("tx_types")
    sources = db.get_list("crm_sources")
    recv = type(db).receive_lead
    recv = getattr(recv, "__wrapped__", recv)            # background job: not tied to who is logged in
    out = {"new": [], "existing": [], "client": [], "skipped": 0, "no_phone": 0}
    with db.atomic():
        for sh in payload.get("sheets", []):
            headers = sh.get("headers", [])
            for values in sh.get("rows", []):
                if values == headers:
                    continue
                m = map_row(sh.get("name", ""), headers, values, services, sources)
                if not m:
                    out["no_phone"] += 1
                    continue
                data, message, ext_id = m
                status, lid = recv(db, data, kind="note", message=message, ext_id=ext_id)
                if status == "skipped":
                    out["skipped"] += 1
                else:
                    out[status].append(lid)
    return out


def mark_seen(db, payload):
    """First connection: remember the rows already in the sheet without adding them."""
    n = 0
    with db.atomic():
        for sh in payload.get("sheets", []):
            for values in sh.get("rows", []):
                m = map_row(sh.get("name", ""), sh.get("headers", []), values)
                if m:
                    db.x("INSERT OR IGNORE INTO crm_imported(ext_id, lead_id, ts) VALUES(?, NULL, datetime('now'))",
                         (m[2],))
                    n += 1
    return n


def count_rows(payload):
    return sum(len(sh.get("rows", [])) for sh in payload.get("sheets", []))
