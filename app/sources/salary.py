"""Salary extraction helpers for public ATS job payloads."""

from __future__ import annotations

import html as html_lib
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup

CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
}

SALARY_KEYWORDS = ("salary", "compensation", "pay", "base")
CURRENCY_CODES = ("USD", "EUR", "GBP", "CAD", "AUD", "NZD")

_MONEY_RE = r"(?:[$€£]\s*)?\d[\d,]*(?:\.\d+)?\s*[kKmM]?"
_RANGE_RE = re.compile(
    rf"{_MONEY_RE}\s*(?:-|–|—|to)\s*{_MONEY_RE}(?:\s*(?:{'|'.join(CURRENCY_CODES)}))?",
    re.IGNORECASE,
)


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _format_amount(value: Any, currency_code: str | None, *, cents: bool = False) -> str | None:
    amount = _as_decimal(value)
    if amount is None:
        return None
    if cents:
        amount = amount / Decimal(100)

    if amount == amount.to_integral():
        rendered = f"{int(amount):,}"
    else:
        rendered = f"{amount:,.2f}".rstrip("0").rstrip(".")

    code = (currency_code or "").upper()
    symbol = CURRENCY_SYMBOLS.get(code)
    if symbol:
        return f"{symbol}{rendered}"
    if code:
        return f"{code} {rendered}"
    return rendered


def format_salary_range(
    min_value: Any,
    max_value: Any,
    currency_code: str | None = None,
    *,
    cents: bool = False,
) -> str | None:
    low = _format_amount(min_value, currency_code, cents=cents)
    high = _format_amount(max_value, currency_code, cents=cents)
    if low is None or high is None:
        return None
    return f"{low}–{high}"


def extract_salary_range_from_text(text: str | None) -> str | None:
    if not text:
        return None
    decoded = html_lib.unescape(text)
    plain = BeautifulSoup(decoded, "html.parser").get_text(" ")
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return None

    for match in _RANGE_RE.finditer(plain):
        candidate = match.group(0).strip(" .,;:")
        window_start = max(match.start() - 80, 0)
        window_end = min(match.end() + 40, len(plain))
        window = plain[window_start:window_end].lower()
        has_currency = any(symbol in candidate for symbol in ("$", "€", "£")) or any(
            code.lower() in candidate.lower() for code in CURRENCY_CODES
        )
        if has_currency or any(keyword in window for keyword in SALARY_KEYWORDS):
            return candidate
    return None


def salary_from_ashby_compensation(compensation: Any) -> str | None:
    if not isinstance(compensation, dict):
        return None

    summary = compensation.get("scrapeableCompensationSalarySummary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()

    components = list(compensation.get("summaryComponents") or [])
    for tier in compensation.get("compensationTiers") or []:
        if isinstance(tier, dict):
            components.extend(tier.get("components") or [])

    for component in components:
        if not isinstance(component, dict):
            continue
        if str(component.get("compensationType") or "").lower() != "salary":
            continue
        salary = format_salary_range(
            component.get("minValue"),
            component.get("maxValue"),
            component.get("currencyCode"),
        )
        if salary:
            return salary
    return None


def salary_from_greenhouse_pay_ranges(pay_input_ranges: Any) -> str | None:
    if not isinstance(pay_input_ranges, list):
        return None

    parts: list[str] = []
    for pay_range in pay_input_ranges:
        if not isinstance(pay_range, dict):
            continue
        salary = format_salary_range(
            pay_range.get("min_cents"),
            pay_range.get("max_cents"),
            pay_range.get("currency_type"),
            cents=True,
        )
        if salary is None:
            continue
        title = pay_range.get("title")
        if isinstance(title, str) and title.strip():
            parts.append(f"{title.strip()}: {salary}")
        else:
            parts.append(salary)
    return "; ".join(parts) or None


def salary_from_greenhouse_metadata(metadata: Any) -> str | None:
    if not isinstance(metadata, list):
        return None

    for field in metadata:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").lower()
        if not any(keyword in name for keyword in SALARY_KEYWORDS):
            continue
        value = field.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            salary = format_salary_range(
                value.get("min_value") or value.get("min"),
                value.get("max_value") or value.get("max"),
                value.get("currency_type") or value.get("currency"),
            )
            if salary:
                return salary
    return None
