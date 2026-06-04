from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
from typing import Any


MAX_FRACTIONAL_LEADING_ZEROS = 5


def number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number_value):
        return None
    return number_value


def format_metric_number(value: Any) -> str:
    number_value = number_or_none(value)
    if number_value is None:
        return ""
    if number_value == 0:
        return "0.00"

    abs_value = abs(number_value)
    if abs_value >= 1:
        return _format_fixed(number_value, 2)

    leading_zeros = _fractional_leading_zeros(abs_value)
    if leading_zeros > MAX_FRACTIONAL_LEADING_ZEROS:
        return "0.00"

    return _format_fixed(number_value, leading_zeros + 2)


def format_metric_value(value: Any, *, prefix: str | None = None, suffix: str | None = None) -> str:
    formatted_value = format_metric_number(value)
    if formatted_value == "":
        return ""
    return apply_number_affixes(formatted_value, prefix=prefix, suffix=suffix)


def format_diff_percent(value: Any) -> str:
    formatted_value = format_metric_number(value)
    if formatted_value == "":
        return ""
    return f"{formatted_value}%"


def format_pvalue(value: Any) -> str:
    number_value = number_or_none(value)
    if number_value is None:
        return ""

    decimals = 3 if number_value < 0.05 else 2
    return _format_fixed(number_value, decimals)


def format_plain_number(value: Any, *, default: str = "") -> str:
    number_value = number_or_none(value)
    if number_value is None:
        return default

    try:
        formatted_value = format(Decimal(str(number_value)), "f")
    except InvalidOperation:
        return default

    if "." in formatted_value:
        formatted_value = formatted_value.rstrip("0").rstrip(".")
    return formatted_value or "0"


def apply_number_affixes(value: str, *, prefix: str | None = None, suffix: str | None = None) -> str:
    value = str(value)
    sign = ""
    if value.startswith("-"):
        sign = "-"
        value = value[1:]
    return f"{sign}{prefix or ''}{value}{suffix or ''}"


def _fractional_leading_zeros(abs_value: float) -> int:
    return max(0, -math.floor(math.log10(abs_value)) - 1)


def _format_fixed(value: float, decimals: int) -> str:
    formatted_value = f"{value:.{decimals}f}"
    if formatted_value.startswith("-0") and float(formatted_value) == 0:
        return formatted_value[1:]
    return formatted_value
