"""
Currency and Amount Normalization Module.
Extracts numeric amounts, separates currency codes, and handles international
number formatting (including Indian numbering commas like 1,95,415.00).
"""
import re
from typing import Tuple, Optional

# Symbols that do not require word boundaries
EXACT_SYMBOLS = {
    "₹": "INR",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "c$": "CAD",
    "a$": "AUD",
    "$": "USD",
}

# Alphanumeric currency codes requiring word boundaries to avoid false positives
WORD_CURRENCY_PATTERNS = [
    (r"\b(?:rs\.?|inr|rupees?)\b", "INR"),
    (r"\b(?:usd|dollars?)\b", "USD"),
    (r"\b(?:eur|euros?)\b", "EUR"),
    (r"\b(?:gbp|pounds?)\b", "GBP"),
    (r"\b(?:aud)\b", "AUD"),
    (r"\b(?:cad)\b", "CAD"),
    (r"\b(?:jpy|yen)\b", "JPY"),
]


def detect_currency(text: str) -> Optional[str]:
    """Detects ISO 4217 currency code from text or symbols with proper token isolation."""
    if not text:
        return None

    # 1. Check exact non-alphanumeric symbols
    for sym, code in EXACT_SYMBOLS.items():
        if sym in text.lower():
            return code

    # 2. Check word boundary patterns
    for pat, code in WORD_CURRENCY_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return code

    return None


def normalize_amount(amount_str: str) -> Optional[float]:
    """
    Parses a string representation of an amount into a float.
    Handles commas, currency symbols, parentheses for negative amounts,
    Indian number formats (e.g., 2,20,762.00), and trailing '/-'.
    """
    if amount_str is None:
        return None

    s = str(amount_str).strip()
    if not s:
        return None

    # Handle negative notation in accounting (123.45)
    is_negative = False
    if s.startswith("(") and s.endswith(")"):
        is_negative = True
        s = s[1:-1].strip()
    elif s.startswith("-"):
        is_negative = True
        s = s[1:].strip()

    # Strip currency symbols and common suffixes
    s = re.sub(r"[₹$€£¥]", "", s)
    s = re.sub(r"(?i)\b(rs\.?|inr|usd|eur|gbp|aud|cad)\b", "", s)
    s = re.sub(r"/-\s*$", "", s)
    s = s.strip()

    # Detect European formatting (e.g. 29.980,02 or 713,81) vs Standard/Indian (e.g. 1,95,415.00 or 1,234.56)
    if "." in s and "," in s:
        if s.rfind(",") > s.rfind("."):
            # European: 1.234,56 -> 1234.56
            s = s.replace(".", "").replace(",", ".")
        else:
            # Standard/Indian: 1,234.56 or 1,95,415.00 -> 1234.56
            s = s.replace(",", "")
    elif "," in s and "." not in s:
        # Single comma: if followed by 1 or 2 digits at end (e.g. 713,81), it is a decimal comma
        if re.search(r",\d{1,2}$", s):
            s = s.replace(",", ".")
        else:
            # Thousands separator without decimals: 10,000 -> 10000
            s = s.replace(",", "")
    else:
        # Standard: periods or integers
        pass

    # Look for numeric float pattern
    match = re.search(r"[-+]?\d*\.?\d+", s)
    if not match:
        return None

    try:
        val = float(match.group(0))
        return -val if is_negative else val
    except ValueError:
        return None
