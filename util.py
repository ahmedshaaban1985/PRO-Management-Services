# -*- coding: utf-8 -*-
"""Small helpers shared by the server and the data layer (no GUI)."""
import datetime as dt
import re


def normalize_date(text):
    """'5/3/2026', '2026-3-5', Arabic digits ... -> '2026-03-05'; '' for empty; None if invalid."""
    s = (str(text) if text is not None else "").strip()
    if not s:
        return ""
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
    else:
        m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", s)
        if not m:
            return None
        d, mo, y = m.groups()
    try:
        return dt.date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None
