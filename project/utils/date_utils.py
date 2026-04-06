"""
Date parsing utilities.

Handles the wide variety of date formats found on news sites and converts
them to a consistent ISO 8601 string (with UTC timezone).
"""
from datetime import datetime, timezone, timedelta
import re

# Ordered from most specific to least specific to avoid mis-parses
_FORMATS = [
    '%Y-%m-%dT%H:%M:%S%z',       # 2026-04-01T12:00:00+03:00
    '%Y-%m-%dT%H:%M:%SZ',         # 2026-04-01T12:00:00Z
    '%Y-%m-%dT%H:%M:%S',          # 2026-04-01T12:00:00
    '%a, %d %b %Y %H:%M:%S %z',   # RFC 2822 with tz
    '%a, %d %b %Y %H:%M:%S GMT',  # RFC 2822 GMT
    '%a, %d %b %Y',               # RFC 2822 date-only (e.g. Sun, 08 Mar 2026)
    '%d-%b-%Y',                   # 1-Apr-2026  (GTA format)
    '%d %b %Y',                   # 1 Apr 2026
    '%B %d, %Y',                  # April 1, 2026
    '%d %B %Y',                   # 1 April 2026
    '%b %d, %Y',                  # Apr 1, 2026
    '%Y-%m-%d',                   # 2026-04-01
    '%d/%m/%Y',                   # 01/04/2026
    '%m/%d/%Y',                   # 04/01/2026
    '%d-%m-%Y',                   # 01-04-2026
    '%d.%m.%Y',                   # 01.04.2026
]


def parse_date(date_str: str) -> str | None:
    """
    Parse an arbitrary date string and return ISO 8601 with UTC offset.
    Returns the original string if no format matches (for resilience).
    Returns None if input is empty/None.
    """
    if not date_str:
        return None
    s = date_str.strip()

    # First try Python's fromisoformat (handles most ISO variants in 3.7+)
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        pass

    # Try each explicit format
    for fmt in _FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue

    # Fallback: return as-is (scraper still runs; cleanup skips articles with unparseable dates)
    return s


def calculate_cutoff(max_age_hours: int) -> datetime:
    """Return the UTC datetime that is max_age_hours before now."""
    return datetime.now(timezone.utc) - timedelta(hours=max_age_hours)


def is_within_cutoff(date_str: str, cutoff: datetime) -> bool:
    """
    Return True if date_str represents a datetime >= cutoff.
    Returns True (inclusive) for empty/unparseable dates to avoid false drops.
    """
    if not date_str:
        return True
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except Exception:
        return True
