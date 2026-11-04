# -*- coding: utf-8 -*-
"""المبلغ بالحروف - amount in words (Arabic & English), for receipts and vouchers."""

_ONES = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة",
         "عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر", "ستة عشر",
         "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
_TENS = ["", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
_HUNDREDS = ["", "مائة", "مائتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة",
             "ثمانمائة", "تسعمائة"]
# (singular, dual, plural 3-10, accusative 11+)
_SCALES = [("", "", "", ""),
           ("ألف", "ألفان", "آلاف", "ألف"),
           ("مليون", "مليونان", "ملايين", "مليون"),
           ("مليار", "ملياران", "مليارات", "مليار")]


def _ar_under_1000(n):
    parts = []
    h, rest = divmod(n, 100)
    if h:
        parts.append(_HUNDREDS[h])
    if rest:
        if rest < 20:
            parts.append(_ONES[rest])
        else:
            t, o = divmod(rest, 10)
            parts.append((_ONES[o] + " و" + _TENS[t]) if o else _TENS[t])
    return " و".join(parts)


def arabic_words(n):
    n = int(n)
    if n == 0:
        return "صفر"
    groups = []
    i = 0
    while n:
        n, g = divmod(n, 1000)
        if g:
            sing, dual, plural, acc = _SCALES[i]
            if i == 0:
                groups.append(_ar_under_1000(g))
            elif g == 1:
                groups.append(sing)
            elif g == 2:
                groups.append(dual)
            elif 3 <= g <= 10:
                groups.append(f"{_ar_under_1000(g)} {plural}")
            else:
                w = _ar_under_1000(g)
                if g == 200:
                    w = "مائتا"          # مائتا ألف
                groups.append(f"{w} {acc}")
        i += 1
    return " و".join(reversed(groups))


_EN_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
            "eighteen", "nineteen"]
_EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _en_under_1000(n):
    h, rest = divmod(n, 100)
    out = []
    if h:
        out.append(f"{_EN_ONES[h]} hundred")
    if rest:
        if rest < 20:
            out.append(_EN_ONES[rest])
        else:
            t, o = divmod(rest, 10)
            out.append(_EN_TENS[t] + (f"-{_EN_ONES[o]}" if o else ""))
    return " and ".join(out) if h and rest else " ".join(out)


def english_words(n):
    n = int(n)
    if n == 0:
        return "zero"
    scales = ["", "thousand", "million", "billion"]
    out, i = [], 0
    while n:
        n, g = divmod(n, 1000)
        if g:
            out.append(_en_under_1000(g) + (f" {scales[i]}" if scales[i] else ""))
        i += 1
    return " ".join(reversed(out))


def amount_in_words(amount, lang="ar", currency="QAR"):
    """1250.5 -> 'فقط ألف ومائتان وخمسون ريال قطري و50 درهم لا غير'"""
    from companies import currency_info
    _, _, name_ar, name_en, sub_ar, sub_en = currency_info(currency)
    # three-decimal currencies (KWD, BHD, OMR) have 1000 sub-units
    scale = 1000 if (currency or "").upper() in ("KWD", "BHD", "OMR") else 100
    amount = round(float(amount or 0), 3 if scale == 1000 else 2)
    riyals = int(amount)
    dirhams = int(round((amount - riyals) * scale))
    if lang == "en":
        s = f"{english_words(riyals).capitalize()} {name_en}"
        if dirhams:
            s += f" and {english_words(dirhams)} {sub_en}"
        return s + " only"
    s = f"فقط {arabic_words(riyals)} {name_ar}"
    if dirhams:
        s += f" و{arabic_words(dirhams)} {sub_ar}"
    return s + " لا غير"
