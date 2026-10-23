"""
Date Normalization Module.
Parses multi-format dates into ISO 8601 (YYYY-MM-DD), resolving ambiguous
day/month representations using locale conventions, document context, or flagging ambiguity.
"""
import re
from datetime import datetime
from typing import Tuple, Optional, List

MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9, "sept": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12
}


def normalize_date(
    date_str: str,
    default_order: str = "DMY",
    context_dates: Optional[List[str]] = None
) -> Tuple[Optional[str], bool, float]:
    """
    Normalizes a date string to ISO 8601 (YYYY-MM-DD).

    Returns:
        Tuple of (iso_date_string, is_ambiguous, confidence)
    """
    if not date_str:
        return None, False, 0.0

    raw = date_str.strip().replace(",", " ").replace(".", "-").replace("/", "-")
    # Clean redundant spaces
    raw = re.sub(r"\s+", " ", raw)

    # 1. Check for named month, e.g. "06-Jul-2025", "1-Jun-25", "08-Feb-2024"
    named_month_match = re.search(
        r"(\d{1,2})[-\s]([a-zA-Z]{3,9})[-\s](\d{2,4})", raw
    )
    if named_month_match:
        day_str, month_str, year_str = named_month_match.groups()
        month_lower = month_str.lower()
        if month_lower in MONTH_NAMES:
            month = MONTH_NAMES[month_lower]
            day = int(day_str)
            year = int(year_str)
            if year < 100:
                year += 2000
            try:
                dt = datetime(year, month, day)
                return dt.strftime("%Y-%m-%d"), False, 0.98
            except ValueError:
                pass

    # Reverse named month, e.g. "Jul 06 2025" or "July 6, 2025"
    named_month_rev = re.search(
        r"([a-zA-Z]{3,9})[-\s](\d{1,2})[-\s](\d{2,4})", raw
    )
    if named_month_rev:
        month_str, day_str, year_str = named_month_rev.groups()
        month_lower = month_str.lower()
        if month_lower in MONTH_NAMES:
            month = MONTH_NAMES[month_lower]
            day = int(day_str)
            year = int(year_str)
            if year < 100:
                year += 2000
            try:
                dt = datetime(year, month, day)
                return dt.strftime("%Y-%m-%d"), False, 0.98
            except ValueError:
                pass

    # 2. Check for numeric YYYY-MM-DD
    iso_match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", raw)
    if iso_match:
        year_str, month_str, day_str = iso_match.groups()
        try:
            dt = datetime(int(year_str), int(month_str), int(day_str))
            return dt.strftime("%Y-%m-%d"), False, 0.99
        except ValueError:
            pass

    # 3. Numeric dates: e.g. "05-08-2025", "03-05-2025", "13-08-2025"
    num_match = re.search(r"\b(\d{1,2})-(\d{1,2})-(\d{2,4})\b", raw)
    if num_match:
        p1, p2, yr = num_match.groups()
        val1, val2, year = int(p1), int(p2), int(yr)
        if year < 100:
            year += 2000

        # If one number is > 12, it must be the day
        if val1 > 12 and val2 <= 12:
            # val1 is day, val2 is month (DMY)
            try:
                dt = datetime(year, val2, val1)
                return dt.strftime("%Y-%m-%d"), False, 0.95
            except ValueError:
                pass
        elif val2 > 12 and val1 <= 12:
            # val2 is day, val1 is month (MDY)
            try:
                dt = datetime(year, val1, val2)
                return dt.strftime("%Y-%m-%d"), False, 0.95
            except ValueError:
                pass
        elif val1 <= 12 and val2 <= 12:
            # Ambiguous date! e.g. 03-05-2025
            is_ambiguous = True
            # Use default_order (DMY for India / Europe, MDY for US)
            if default_order == "DMY":
                day, month = val1, val2
            else:
                month, day = val1, val2

            try:
                dt = datetime(year, month, day)
                # Confidence is lowered due to ambiguity
                return dt.strftime("%Y-%m-%d"), is_ambiguous, 0.70
            except ValueError:
                pass

    return None, False, 0.0
