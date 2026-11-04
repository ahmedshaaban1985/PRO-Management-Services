# -*- coding: utf-8 -*-
"""إرسال رسائل واتساب أوتوماتيك عن طريق WhatsApp Cloud API (ميتا).

Needs, from the Meta for Developers / WhatsApp Manager pages:
  * a permanent access token (System User token) with whatsapp_business_messaging
  * the Phone number ID of the business number
  * an APPROVED message template (category UTILITY) with 3 body parameters
Business-initiated messages outside the 24-hour customer window can only be templates.
"""
import json
import ssl
import urllib.error
import urllib.request

DEFAULT_VERSION = "v23.0"


def send_template(token, phone_id, to, template, lang="ar", params=(), version=None, timeout=30):
    """Returns (True, message_id) or (False, error text)."""
    url = f"https://graph.facebook.com/{version or DEFAULT_VERSION}/{phone_id}/messages"
    body = {"messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": template, "language": {"code": lang or "ar"},
                         "components": [{"type": "body", "parameters": [
                             {"type": "text", "text": str(p).replace("\n", " ").replace("\t", " ")[:1000]}
                             for p in params]}] if params else []}}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            res = json.loads(r.read().decode("utf-8") or "{}")
        mid = (res.get("messages") or [{}])[0].get("id", "")
        return True, mid
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8")).get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or str(e)
            code = err.get("code")
            return False, f"{code}: {msg}" if code else msg
        except Exception:       # noqa: BLE001
            return False, str(e)
    except Exception as e:      # noqa: BLE001
        return False, str(e)
