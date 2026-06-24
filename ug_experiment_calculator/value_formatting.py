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


def format_metric_number(value: Any, *, thousands_separator: bool = True) -> str:
    number_value = number_or_none(value)
    if number_value is None:
        return ""
    if number_value == 0:
        return add_thousands_separator("0.00") if thousands_separator else "0.00"

    abs_value = abs(number_value)
    if abs_value >= 1:
        return _format_with_optional_thousands(_format_fixed(number_value, 2), thousands_separator=thousands_separator)

    leading_zeros = _fractional_leading_zeros(abs_value)
    if leading_zeros > MAX_FRACTIONAL_LEADING_ZEROS:
        return "0.00"

    return _format_with_optional_thousands(
        _format_fixed(number_value, leading_zeros + 2),
        thousands_separator=thousands_separator,
    )


def format_metric_value(
    value: Any,
    *,
    prefix: str | None = None,
    suffix: str | None = None,
    thousands_separator: bool = True,
) -> str:
    formatted_value = format_metric_number(value, thousands_separator=thousands_separator)
    if formatted_value == "":
        return ""
    return apply_number_affixes(formatted_value, prefix=prefix, suffix=suffix)


def format_diff_percent(value: Any, *, thousands_separator: bool = True) -> str:
    formatted_value = format_metric_number(value, thousands_separator=thousands_separator)
    if formatted_value == "":
        return ""
    return f"{formatted_value}%"


def format_pvalue(value: Any, *, thousands_separator: bool = True) -> str:
    number_value = number_or_none(value)
    if number_value is None:
        return ""

    decimals = 3 if number_value < 0.05 else 2
    return _format_with_optional_thousands(_format_fixed(number_value, decimals), thousands_separator=thousands_separator)


def format_plain_number(value: Any, *, default: str = "", thousands_separator: bool = True) -> str:
    number_value = number_or_none(value)
    if number_value is None:
        return default

    try:
        formatted_value = format(Decimal(str(number_value)), "f")
    except InvalidOperation:
        return default

    if "." in formatted_value:
        formatted_value = formatted_value.rstrip("0").rstrip(".")
    formatted_value = formatted_value or "0"
    return _format_with_optional_thousands(formatted_value, thousands_separator=thousands_separator)


def format_integer_value(value: Any, *, default: str = "", thousands_separator: bool = True) -> str:
    number_value = number_or_none(value)
    if number_value is None:
        return default
    return _format_with_optional_thousands(str(int(round(number_value))), thousands_separator=thousands_separator)


def apply_number_affixes(value: str, *, prefix: str | None = None, suffix: str | None = None) -> str:
    value = str(value)
    sign = ""
    if value.startswith("-"):
        sign = "-"
        value = value[1:]
    return f"{sign}{prefix or ''}{value}{suffix or ''}"


def add_thousands_separator(value: str) -> str:
    value = str(value)
    sign = ""
    if value.startswith("-"):
        sign = "-"
        value = value[1:]

    integer_part, dot, fractional_part = value.partition(".")
    if not integer_part.isdigit():
        return f"{sign}{value}"

    groups = []
    while len(integer_part) > 3:
        groups.append(integer_part[-3:])
        integer_part = integer_part[:-3]
    groups.append(integer_part)

    grouped_integer = ",".join(reversed(groups))
    return f"{sign}{grouped_integer}{dot}{fractional_part}"


def _format_with_optional_thousands(value: str, *, thousands_separator: bool) -> str:
    if not thousands_separator or value == "":
        return value
    return add_thousands_separator(value)


def _fractional_leading_zeros(abs_value: float) -> int:
    return max(0, -math.floor(math.log10(abs_value)) - 1)


def _format_fixed(value: float, decimals: int) -> str:
    formatted_value = f"{value:.{decimals}f}"
    if formatted_value.startswith("-0") and float(formatted_value) == 0:
        return formatted_value[1:]
    return formatted_value
