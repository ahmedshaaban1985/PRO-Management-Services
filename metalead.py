# -*- coding: utf-8 -*-
"""ربط مباشر مع إعلانات فيسبوك وانستغرام (Meta Lead Ads - Instant Forms).

The program asks Meta's Graph API every few minutes for the new people who filled the
lead forms of the company's Facebook page (Instagram lead ads use the same forms; each
lead says whether it came from "fb" or "ig").  No middle service, no monthly cost.

One-time setup (done from Settings):
  * a Meta developer app (stays in Development mode - it only reads the owner's own page,
    which Meta allows for people who have a role in the app)
  * a user token from Graph API Explorer with: pages_show_list, pages_read_engagement,
    pages_manage_ads, leads_retrieval
  * the program swaps it for a long-lived token and keeps the PAGE token (it does not expire
    unless the password changes / the app is removed).

Everything here is pure Python (runs inside a worker thread); the database work is in
import_leads(), which runs on the UI thread.
"""
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import weblead

VERSION = "v23.0"
GRAPH = "https://graph.facebook.com/"
LEAD_FIELDS = "id,created_time,field_data,ad_name,adset_name,campaign_name,form_id,platform,is_organic"
PERMISSIONS = "pages_show_list,pages_read_engagement,pages_manage_ads,leads_retrieval,ads_management"


class MetaError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


def _default_opener(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "CreativeReps"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
        return r.read().decode("utf-8")


def _explain(err):
    code = err.get("code")
    sub = err.get("error_subcode")
    text = err.get("error_user_msg") or err.get("message") or ""
    if code == 190 or sub in (460, 463, 467):
        return ("صلاحية الربط مع فيسبوك انتهت أو اتلغت (غالبًا اتغيّرت كلمة سر الفيسبوك). "
                "ادخل الإعدادات واعمل «ربط / تغيير الصفحة» تاني.")
    if code in (10, 200, 3) or "permission" in text.lower() or "leads_retrieval" in text:
        return ("فيسبوك رفض قراية العملاء: الصلاحيات ناقصة. لازم التوكن يكون فيه leads_retrieval و pages_manage_ads، "
                "ولو حددت مين يشوف العملاء من Business Suite ← الإعدادات ← الوصول للعملاء المحتملين (Leads access) "
                "ضيف نفسك والتطبيق هناك.  [" + text[:160] + "]")
    if code in (4, 17, 32, 613):
        return "فيسبوك طلب نهدّي الطلبات شوية (حد الاستخدام). البرنامج هيحاول تاني بعد شوية."
    return f"خطأ من فيسبوك ({code}): {text[:240]}"


def call(path, params, opener=None, version=None):
    """GET graph.facebook.com/<version>/<path> -> JSON dict. Raises MetaError (Arabic message)."""
    if path.startswith("https://"):
        url = path                       # a "paging.next" link
    else:
        url = GRAPH + (version or VERSION) + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(params)
    try:
        raw = (opener or _default_opener)(url)
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8")).get("error", {})
        except Exception:                # noqa: BLE001
            err = {"message": str(e)}
        raise MetaError(_explain(err), err.get("code")) from e
    except urllib.error.URLError as e:
        raise MetaError("مفيش اتصال بالإنترنت أو فيسبوك مش بيرد.") from e
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise MetaError("فيسبوك رجّع رد مش مفهوم.") from e
    if isinstance(data, dict) and data.get("error"):
        raise MetaError(_explain(data["error"]), data["error"].get("code"))
    return data


def _all_pages(first, opener, max_pages=30):
    out, data, n = [], first, 0
    while True:
        out += data.get("data", [])
        nxt = (data.get("paging") or {}).get("next")
        n += 1
        if not nxt or n >= max_pages:
            return out
        data = call(nxt, None, opener)


# ---------------------------------------------------------------- connecting
def long_lived_user_token(app_id, app_secret, short_token, opener=None):
    j = call("oauth/access_token", {"grant_type": "fb_exchange_token", "client_id": app_id,
                                    "client_secret": app_secret, "fb_exchange_token": short_token}, opener)
    tok = j.get("access_token")
    if not tok:
        raise MetaError("فيسبوك ما رجّعش توكن طويل. اتأكد من App ID و App Secret.")
    return tok


def missing_permissions(user_token, opener=None):
    """Which of PERMISSIONS the token was NOT granted (empty list = all good)."""
    j = call("me/permissions", {"access_token": user_token, "limit": 200}, opener)
    granted = {p.get("permission") for p in j.get("data", []) if p.get("status") == "granted"}
    return [p for p in PERMISSIONS.split(",") if p not in granted]


def missing_message(missing):
    return ("التوكن ناقصه الصلاحيات دي: " + "، ".join(missing) + "\n\n"
            "الحل:\n"
            "1) لو الصلاحية مش ظاهرة أصلًا في قايمة Permissions في Graph API Explorer: ادخل التطبيق على "
            "developers.facebook.com ← Use cases ← Add use case ← اختار «Capture & manage ad leads with Marketing API» "
            "واعمله Customize وفعّل الصلاحيات.\n"
            "2) في Graph API Explorer ضيف الصلاحيات الناقصة، ودوس Generate Access Token تاني، ولما تطلع نافذة فيسبوك "
            "دوس «Edit access / تعديل الإعدادات» واختار صفحة الشركة وحساب الإعلانات ووافق على كل حاجة.\n"
            "3) انسخ التوكن الجديد والصقه في البرنامج.")


def list_pages(user_token, opener=None):
    """-> [{"id", "name", "access_token"}] - the pages this person manages."""
    j = call("me/accounts", {"fields": "id,name,access_token,tasks", "limit": 100, "access_token": user_token},
             opener)
    pages = [p for p in _all_pages(j, opener) if p.get("access_token")]
    if not pages:
        raise MetaError("مفيش صفحات ظاهرة للحساب ده. لما تعمل Generate Access Token اختار صفحة الشركة "
                        "ووافق على كل الصلاحيات.")
    return pages


def list_forms(page_id, page_token, opener=None):
    j = call(f"{page_id}/leadgen_forms", {"fields": "id,name,status,leads_count", "limit": 100,
                                          "access_token": page_token}, opener)
    return _all_pages(j, opener)


def fetch_leads(page_id, page_token, since_ts, opener=None):
    """All leads newer than since_ts (unix seconds) from every form of the page.
    -> {"forms": [...], "leads": [(lead, form_name)], "max_ts": int}"""
    forms = list_forms(page_id, page_token, opener)
    flt = json.dumps([{"field": "time_created", "operator": "GREATER_THAN", "value": int(since_ts)}])
    leads, max_ts = [], int(since_ts)
    for f in forms:
        j = call(f"{f['id']}/leads", {"fields": LEAD_FIELDS, "filtering": flt, "limit": 100,
                                      "access_token": page_token}, opener)
        for ld in _all_pages(j, opener):
            leads.append((ld, f.get("name", "")))
            max_ts = max(max_ts, created_ts(ld))
    return {"forms": forms, "leads": leads, "max_ts": max_ts}


def created_ts(lead):
    """'2026-10-11T19:55:02+0000' -> unix seconds (0 if missing)."""
    s = (lead.get("created_time") or "").strip()
    if not s:
        return 0
    try:
        import datetime as dt
        return int(dt.datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z").timestamp())
    except ValueError:
        return 0


# ---------------------------------------------------------------- into the CRM
def map_lead(lead, form_name, services=(), sources=()):
    """One Meta lead -> (data, message, ext_id) or None when it has no phone."""
    headers, values = [], []
    for f in lead.get("field_data") or []:
        headers.append(str(f.get("name") or ""))
        values.append(", ".join(str(v) for v in (f.get("values") or [])))
    plat = (lead.get("platform") or "").lower()
    headers.append("platform")
    values.append(plat)
    m = weblead.map_row(form_name or "Lead form", headers, values, services, sources)
    if not m:
        return None
    data, _msg, _ = m

    def src(ar_label):
        return next((s for s in sources if s.split("|")[0] == ar_label), ar_label)
    data["source"] = src("إعلانات انستغرام") if plat in ("ig", "instagram") else src("إعلانات فيسبوك")
    used = set(weblead.PHONE_KEYS + weblead.NAME_KEYS + weblead.EMAIL_KEYS + weblead.FIRST_KEYS +
               weblead.LAST_KEYS + weblead.COMPANY_KEYS + weblead.PLATFORM_KEYS)
    extra = [f"{h.replace('_', ' ')}: {v}" for h, v in zip(headers, values)
             if v and weblead._norm(h) not in used]
    where = " / ".join(x for x in (lead.get("campaign_name"), lead.get("ad_name")) if x)
    message = (f"من {data['source'].split('|')[0]} — فورم «{form_name}»" + (f" — إعلان: {where}" if where else "")
               + (" — " + " | ".join(extra) if extra else ""))
    return data, message, "meta:" + str(lead.get("id"))


def import_leads(db, result):
    """Add fetch_leads() output to the CRM -> summary like weblead.import_payload, plus
    "fresh": [lead ids that are brand-new people and arrived in the last 24 hours]."""
    services = db.get_list("tx_types")
    sources = db.get_list("crm_sources")
    recv = type(db).receive_lead
    recv = getattr(recv, "__wrapped__", recv)            # background job: not tied to who is logged in
    out = {"new": [], "existing": [], "client": [], "skipped": 0, "no_phone": 0, "fresh": []}
    now = time.time()
    with db.atomic():
        for lead, form_name in result.get("leads", []):
            m = map_lead(lead, form_name, services, sources)
            if not m:
                out["no_phone"] += 1
                continue
            data, message, ext_id = m
            status, lid = recv(db, data, kind="note", message=message, ext_id=ext_id)
            if status == "skipped":
                out["skipped"] += 1
                continue
            out[status].append(lid)
            if status in ("new", "client") and now - created_ts(lead) < 24 * 3600:
                out["fresh"].append(lid)
    return out
