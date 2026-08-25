"""Regex building blocks and vocabulary for the signal parser.

Everything the parser knows about wording lives here, so adding support for a
new phrasing normally means editing this file only (section 13: the parser must
be easy to extend with new patterns).
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------- numbers
# Accepts 3340, 3340.5, 3,340.50 — but not the "1" in "TP1".
NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,4})?|\d{2,7}(?:\.\d{1,4})?"
NUMBER_RE = re.compile(NUMBER)

# --------------------------------------------------------------------- symbols
SYMBOL_ALIASES: dict[str, str] = {
    "GOLD": "XAUUSD",
    "XAUUSD": "XAUUSD",
    "XAU": "XAUUSD",
    "XAU/USD": "XAUUSD",
    "GOLDSPOT": "XAUUSD",
    "SPOTGOLD": "XAUUSD",
    "ทอง": "XAUUSD",
    "ทองคำ": "XAUUSD",
    "SILVER": "XAGUSD",
    "XAGUSD": "XAGUSD",
    "XAG": "XAGUSD",
    "US30": "US30",
    "DOWJONES": "US30",
    "NAS100": "NAS100",
    "NASDAQ": "NAS100",
    "BTC": "BTCUSD",
    "BTCUSD": "BTCUSD",
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
}
SYMBOL_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(k) for k in SYMBOL_ALIASES), key=len, reverse=True)) + r")\b",
    re.IGNORECASE | re.UNICODE,
)

# ------------------------------------------------------------------ directions
BUY_WORDS = ["BUY", "LONG", "BULLISH", "ซื้อ"]
SELL_WORDS = ["SELL", "SHORT", "BEARISH", "ขาย"]

# "BUY LIMIT", "SELL STOP", "BUY NOW" all reduce to a plain direction.
_ORDER_QUALIFIER = r"(?:\s*(?:LIMIT|STOP|NOW|ZONE|SETUP|SIGNAL|ENTRY))?"
DIRECTION_RE = re.compile(
    r"(?<![A-Z])(?P<dir>" + "|".join(BUY_WORDS + SELL_WORDS) + r")(?![A-Z])" + _ORDER_QUALIFIER,
    re.IGNORECASE | re.UNICODE,
)

# ------------------------------------------------------------------ level tags
STOP_LOSS_RE = re.compile(
    r"(?:S\s*[/.\-]?\s*L|STOP\s*-?\s*LOSS|STOPLOSS|STOP)\b\s*(?:@|AT|IS|:|=|-)?\s*(?P<value>" + NUMBER + r")",
    re.IGNORECASE,
)

# Matches "TP 3350", "TP1 3350", "TP1: 3350", "TAKE PROFIT 3 3370" and captures
# any run of following numbers so "TP 3350 3360 3370" yields three targets.
TAKE_PROFIT_RE = re.compile(
    # The index digit must stand alone: in "TP 3330" the leading 3 is a price.
    r"(?:T\s*[/.\-]?\s*P|TAKE\s*-?\s*PROFIT|TARGET)\s*(?P<index>[1-9](?!\d))?\s*(?:@|AT|IS|:|=|-)?\s*"
    r"(?P<values>(?:" + NUMBER + r")(?:\s*[,/&]?\s*(?:" + NUMBER + r"))*)",
    re.IGNORECASE,
)

ENTRY_RE = re.compile(
    r"(?:ENTRY|ENTER|OPEN|PRICE|@)\s*(?:PRICE|POINT|AT|:|=)?\s*(?P<value>" + NUMBER + r")",
    re.IGNORECASE,
)

# "BUY GOLD 3340", "SELL 3340", "BUY GOLD @ 3340-3342"
DIRECTION_ENTRY_RE = re.compile(
    r"(?P<dir>" + "|".join(BUY_WORDS + SELL_WORDS) + r")"
    + _ORDER_QUALIFIER
    + r"\s*(?P<symbol>[A-Z/]{2,10}|ทองคำ|ทอง)?\s*(?:@|AT|:|=)?\s*(?P<value>" + NUMBER + r")",
    re.IGNORECASE | re.UNICODE,
)

# Entry given as a zone: "3340-3342" or "3340 - 3342".
RANGE_RE = re.compile(r"(?P<low>" + NUMBER + r")\s*[-–/]\s*(?P<high>" + NUMBER + r")")

# ------------------------------------------------------- trade-management text
# These update an existing trade; they must never create a new signal.
MANAGEMENT_RE = re.compile(
    r"\b(?:CLOSE|CLOSED|CLOSING|EXIT|(?:TP|SL)\s*[1-9]?\s*HIT|HIT\s*(?:TP|SL)\s*[1-9]?|BREAK\s*EVEN|BREAKEVEN|"
    r"MOVE\s*(?:THE\s*)?SL|SL\s*TO\s*BE|SECURE|PARTIAL|BOOK\s*PROFIT|CANCEL|CANCELLED|NO\s*TRADE|RUNNING)\b"
    # Thai has no ASCII word boundaries, so those alternatives are matched bare.
    r"|ปิดออเดอร์|ปิดไม้|ยกเลิก",
    re.IGNORECASE | re.UNICODE,
)

# Emoji / decoration that appears between a label and its number.
NOISE_RE = re.compile(
    "[←-⯿☀-➿️‍•\U0001f000-\U0001faff*_`~]+",
    re.UNICODE,
)


def normalize(text: str) -> str:
    """Collapse decoration so the level regexes see plain text.

    Line structure is preserved: some groups post one level per line and the
    line breaks make the numbers easier to attribute.
    """
    if not text:
        return ""
    cleaned = NOISE_RE.sub(" ", text)
    cleaned = cleaned.replace("：", ":").replace("＠", "@").replace("–", "-").replace("—", "-")
    # Normalise spacing but keep newlines.
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    return cleaned.strip()


def to_number(raw: str) -> float | None:
    try:
        return float(raw.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def canonical_symbol(raw: str | None) -> str | None:
    if not raw:
        return None
    key = raw.upper().replace(" ", "").replace("-", "")
    return SYMBOL_ALIASES.get(key)


def direction_of(word: str) -> str | None:
    upper = word.upper()
    if upper in {w.upper() for w in BUY_WORDS}:
        return "BUY"
    if upper in {w.upper() for w in SELL_WORDS}:
        return "SELL"
    return None
