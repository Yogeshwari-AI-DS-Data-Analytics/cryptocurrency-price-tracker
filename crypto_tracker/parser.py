"""Pure text-to-number helpers.

This module has no Selenium and no pandas imports, which keeps it easy to unit
test (see tests/test_parser.py).
"""

import re
from typing import Optional

# Multipliers for the suffixes CoinMarketCap uses on large numbers.
_SUFFIX_MULTIPLIERS = {
    "k": 1e3,
    "m": 1e6,
    "b": 1e9,
    "t": 1e12,
}

# Characters stripped before parsing: currency symbols, thin/normal spaces
# (including non-breaking ones), thousands separators and percent signs.
_STRIP_CHARS = re.compile(r"[^\d.,\-+eE]")

# Unicode minus / dash variants that appear in percentages.
_DASHES = {
    "\u2212": "-",  # minus sign
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2012": "-",  # figure dash
}

_PLACEHOLDER_VALUES = {"", "-", "--", "n/a", "na", "?", "null", "none", "..."}


def _clean(text: Optional[str]) -> str:
    """Unify dashes and collapse whitespace, preserving letter case."""
    if text is None:
        return ""
    value = str(text).strip()
    for dash, replacement in _DASHES.items():
        value = value.replace(dash, replacement)
    return re.sub(r"\s+", " ", value).strip()


def _normalize(text: Optional[str]) -> str:
    """Like :func:`_clean`, but lower-cased for numeric parsing."""
    return _clean(text).lower()


def _to_float(text: Optional[str]) -> Optional[float]:
    """Parse a cleaned numeric string, tolerating ``1,234.56`` and ``1.234,56``."""
    value = _STRIP_CHARS.sub("", _normalize(text))
    if not value or value in {"+", "-", ".", "-."}:
        return None

    # Decide which character is the decimal separator.
    if "," in value and "." in value:
        # Whichever separator comes last is the decimal one.
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        # A single comma with exactly three digits after it is a thousands
        # separator; otherwise it is a decimal comma.
        head, _, tail = value.rpartition(",")
        value = value.replace(",", "") if len(tail) == 3 else value.replace(",", ".")

    try:
        return float(value)
    except ValueError:
        return None


def parse_price(text: Optional[str]) -> Optional[float]:
    """Convert ``"$64,123.45"`` or ``"$0.00001234"`` to ``64123.45``."""
    normalized = _normalize(text)
    if normalized in _PLACEHOLDER_VALUES:
        return None

    value = _to_float(normalized)
    if value is None:
        return None

    suffix = next((char for char in normalized if char in _SUFFIX_MULTIPLIERS), None)
    if suffix:
        value *= _SUFFIX_MULTIPLIERS[suffix]
    return value


def parse_percent(text: Optional[str]) -> Optional[float]:
    """Convert ``"+2.45%"`` / ``"-0.87 %"`` / ``"2.45"`` to ``2.45`` / ``-0.87``."""
    normalized = _normalize(text)
    if normalized in _PLACEHOLDER_VALUES:
        return None
    return _to_float(normalized)


def parse_market_cap(text: Optional[str]) -> Optional[float]:
    """Convert ``"$1.23T"`` / ``"$2,375.4B"`` to ``1230000000000.0``.

    Small caps are shown in plain dollars, e.g. ``"$845,120"``.
    """
    return parse_price(text)


def parse_rank(text: Optional[str]) -> Optional[int]:
    """Convert ``"1"`` / ``"#1"`` to ``1``.

    Used to discard non-coin rows such as ad and "Explore assets" banners,
    whose text never parses as an integer.
    """
    normalized = _normalize(text)
    if normalized in _PLACEHOLDER_VALUES:
        return None
    value = _to_float(normalized)
    if value is None:
        return None
    return int(value)


def parse_symbol(text: Optional[str]) -> Optional[str]:
    """Clean a ticker such as ``"BTC"`` or ``"\\nBTC\\n"`` to ``"BTC"``."""
    value = _clean(text)
    if value.lower() in _PLACEHOLDER_VALUES:
        return None
    return value.upper()


def parse_name(text: Optional[str]) -> Optional[str]:
    """Clean a coin name such as ``"  Bitcoin "`` to ``"Bitcoin"``.

    Letter case is preserved because names are display data, not keys.
    """
    value = _clean(text)
    if value.lower() in _PLACEHOLDER_VALUES:
        return None
    return value