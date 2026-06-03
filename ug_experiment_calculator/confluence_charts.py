from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping
from html import escape
from typing import Any, Literal, Optional

import numpy as np
import pandas as pd

from .config import ExperimentCalculatorConfig
from .echarts import ECHARTS_COLOR_RGB_VALUES
from .value_formatting import format_plain_number


CONFLUENCE_DATE_FORMAT = "yyyy-MM-dd"
SIGNIFICANCE_LEVEL_SERIES_NAME = "α = 0.05"
SIGNIFICANCE_LEVEL_COLOR = "#ff0000"
PVALUE_CHART_SUBTITLE = "p-value"
DIFF_CHART_SUBTITLE = "diff, %"

CONFLUENCE_CHART_BASE_COLUMNS: tuple[str, ...] = (
    "dt",
    "variation_pair",
)


def get_metric_confluence_chart_data(
    exp_id: int,
    metric: str,
    client: str,
    segment: str,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    from clickhouse_worker import clickhouse_string_literal as _clickhouse_string_literal
    from clickhouse_worker import execute_sql

    cfg = config or ExperimentCalculatorConfig.from_env()
    query = f"""
        select
            `dt`,
            `metric`,
            `variation_pair`,
            `control_variation`,
            `test_variation`,
            `lift`,
            `pvalue`
        from {cfg.exp_results_table}
        where
            `exp_id` = {int(exp_id)}
        and `metric` = {_clickhouse_string_literal(metric)}
        and `client` = {_clickhouse_string_literal(client)}
        and `segment` = {_clickhouse_string_literal(segment)}
        order by
            `dt`,
            `control_variation`,
            `test_variation`
    """
    return execute_sql(query)


def build_metric_confluence_chart_code(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    metric: str,
    *,
    output_format: Literal["storage", "wiki"] = "storage",
    width: int = 250,
    height: int = 125,
    include_significance_level: bool = True,
    significance_level: float = 0.05,
    max_x_ticks: int = 2,
    title_placement: Literal["subtitle", "title", "none"] = "subtitle",
    title: str = PVALUE_CHART_SUBTITLE,
    image_format: str = "png",
) -> str:
    chart_data = _prepare_chart_data(
        rows,
        value_column="pvalue",
        constant_series_name=SIGNIFICANCE_LEVEL_SERIES_NAME if include_significance_level else None,
        constant_value=significance_level if include_significance_level else None,
    )
    domain_axis_tick_unit = _domain_axis_tick_unit(chart_data.dates, max_x_ticks=max_x_ticks)

    if output_format == "storage":
        return _build_storage_chart_code(
            chart_data,
            title=title,
            width=width,
            height=height,
            domain_axis_tick_unit=domain_axis_tick_unit,
            title_placement=title_placement,
            image_format=image_format,
            range_axis_lower_bound=0,
            range_axis_tick_unit=0.25,
        )
    if output_format == "wiki":
        return _build_wiki_chart_code(
            chart_data,
            title=title,
            width=width,
            height=height,
            domain_axis_tick_unit=domain_axis_tick_unit,
            title_placement=title_placement,
            image_format=image_format,
            range_axis_lower_bound=0,
            range_axis_tick_unit=0.25,
        )

    raise ValueError(f"Unsupported output_format: {output_format}")


def build_metric_confluence_lift_chart_code(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    metric: str,
    *,
    output_format: Literal["storage", "wiki"] = "storage",
    width: int = 250,
    height: int = 125,
    max_x_ticks: int = 2,
    title_placement: Literal["subtitle", "title", "none"] = "subtitle",
    title: str = DIFF_CHART_SUBTITLE,
    image_format: str = "png",
) -> str:
    chart_data = _prepare_chart_data(
        rows,
        value_column="lift",
        constant_series_name=None,
        constant_value=None,
    )
    domain_axis_tick_unit = _domain_axis_tick_unit(chart_data.dates, max_x_ticks=max_x_ticks)

    if output_format == "storage":
        return _build_storage_chart_code(
            chart_data,
            title=title,
            width=width,
            height=height,
            domain_axis_tick_unit=domain_axis_tick_unit,
            title_placement=title_placement,
            image_format=image_format,
        )
    if output_format == "wiki":
        return _build_wiki_chart_code(
            chart_data,
            title=title,
            width=width,
            height=height,
            domain_axis_tick_unit=domain_axis_tick_unit,
            title_placement=title_placement,
            image_format=image_format,
        )

    raise ValueError(f"Unsupported output_format: {output_format}")


def get_metric_confluence_chart_code(
    exp_id: int,
    metric: str,
    client: str,
    segment: str,
    *,
    output_format: Literal["storage", "wiki"] = "storage",
    width: int = 250,
    height: int = 125,
    include_significance_level: bool = True,
    significance_level: float = 0.05,
    max_x_ticks: int = 2,
    title_placement: Literal["subtitle", "title", "none"] = "subtitle",
    title: str = PVALUE_CHART_SUBTITLE,
    image_format: str = "png",
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    rows = get_metric_confluence_chart_data(exp_id, metric, client, segment, config=config)
    return build_metric_confluence_chart_code(
        rows,
        metric,
        output_format=output_format,
        width=width,
        height=height,
        include_significance_level=include_significance_level,
        significance_level=significance_level,
        max_x_ticks=max_x_ticks,
        title_placement=title_placement,
        title=title,
        image_format=image_format,
    )


def get_metric_confluence_lift_chart_code(
    exp_id: int,
    metric: str,
    client: str,
    segment: str,
    *,
    output_format: Literal["storage", "wiki"] = "storage",
    width: int = 250,
    height: int = 125,
    max_x_ticks: int = 2,
    title_placement: Literal["subtitle", "title", "none"] = "subtitle",
    title: str = DIFF_CHART_SUBTITLE,
    image_format: str = "png",
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    rows = get_metric_confluence_chart_data(exp_id, metric, client, segment, config=config)
    return build_metric_confluence_lift_chart_code(
        rows,
        metric,
        output_format=output_format,
        width=width,
        height=height,
        max_x_ticks=max_x_ticks,
        title_placement=title_placement,
        title=title,
        image_format=image_format,
    )


class _ChartData:
    def __init__(self, dates: list[str], series_names: list[str], values_by_date: dict[str, dict[str, float | None]]):
        self.dates = dates
        self.series_names = series_names
        self.values_by_date = values_by_date


def _prepare_chart_data(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    value_column: str,
    constant_series_name: str | None,
    constant_value: float | None,
) -> _ChartData:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    required_columns = (*CONFLUENCE_CHART_BASE_COLUMNS, value_column)

    if df.empty:
        for column in required_columns:
            if column not in df.columns:
                df[column] = pd.Series(dtype="object")
        return _ChartData(dates=[], series_names=[], values_by_date={})

    missing_columns = set(required_columns).difference(df.columns)
    if missing_columns:
        missing_columns_str = ", ".join(sorted(missing_columns))
        raise ValueError(f"Missing Confluence chart columns: {missing_columns_str}")

    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt", "variation_pair"]).copy()
    df["variation_pair"] = df["variation_pair"].astype(str)
    df[value_column] = pd.to_numeric(df[value_column], errors="coerce")

    sort_columns = ["variation_pair", "dt"]
    if {"control_variation", "test_variation"}.issubset(df.columns):
        sort_columns = ["control_variation", "test_variation", "dt"]
    df = df.sort_values(sort_columns).reset_index(drop=True)
    df["date_value"] = df["dt"].map(_date_value)

    dates = sorted(df["date_value"].dropna().unique().tolist())
    series_names = [str(name) for name in df["variation_pair"].drop_duplicates().tolist()]
    if constant_series_name is not None:
        series_names.append(constant_series_name)

    values_by_date: dict[str, dict[str, float | None]] = {date: {} for date in dates}
    for _, row in df.iterrows():
        values_by_date[row["date_value"]][row["variation_pair"]] = _number_or_none(row[value_column])

    if constant_series_name is not None:
        constant_number = _number_or_none(constant_value)
        for date in dates:
            values_by_date[date][constant_series_name] = constant_number

    return _ChartData(dates=dates, series_names=series_names, values_by_date=values_by_date)


def _build_storage_chart_code(
    chart_data: _ChartData,
    *,
    title: str,
    width: int,
    height: int,
    domain_axis_tick_unit: int,
    title_placement: Literal["subtitle", "title", "none"],
    image_format: str,
    range_axis_lower_bound: float | None = None,
    range_axis_upper_bound: float | None = None,
    range_axis_tick_unit: float | None = None,
) -> str:
    parameters = _chart_parameters(
        title=title,
        width=width,
        height=height,
        domain_axis_tick_unit=domain_axis_tick_unit,
        title_placement=title_placement,
        image_format=image_format,
        colors=_series_colors(chart_data.series_names),
        range_axis_lower_bound=range_axis_lower_bound,
        range_axis_upper_bound=range_axis_upper_bound,
        range_axis_tick_unit=range_axis_tick_unit,
    )

    lines = ['<ac:structured-macro ac:name="chart">']
    for name, value in parameters:
        lines.append(f'  <ac:parameter ac:name="{escape(name)}">{escape(str(value))}</ac:parameter>')
    lines.append("  <ac:rich-text-body>")
    lines.extend(_storage_table_lines(chart_data))
    lines.append("  </ac:rich-text-body>")
    lines.append("</ac:structured-macro>")
    return "\n".join(lines)


def _build_wiki_chart_code(
    chart_data: _ChartData,
    *,
    title: str,
    width: int,
    height: int,
    domain_axis_tick_unit: int,
    title_placement: Literal["subtitle", "title", "none"],
    image_format: str,
    range_axis_lower_bound: float | None = None,
    range_axis_upper_bound: float | None = None,
    range_axis_tick_unit: float | None = None,
) -> str:
    parameters = _chart_parameters(
        title=title,
        width=width,
        height=height,
        domain_axis_tick_unit=domain_axis_tick_unit,
        title_placement=title_placement,
        image_format=image_format,
        colors=_series_colors(chart_data.series_names),
        range_axis_lower_bound=range_axis_lower_bound,
        range_axis_upper_bound=range_axis_upper_bound,
        range_axis_tick_unit=range_axis_tick_unit,
    )
    params_str = "|".join(f"{name}={value}" for name, value in parameters)
    lines = [f"{{chart:{params_str}}}"]
    lines.extend(_wiki_table_lines(chart_data))
    lines.append("{chart}")
    return "\n".join(lines)


def _chart_parameters(
    *,
    title: str,
    width: int,
    height: int,
    domain_axis_tick_unit: int,
    title_placement: Literal["subtitle", "title", "none"],
    image_format: str,
    colors: list[str],
    range_axis_lower_bound: float | None,
    range_axis_upper_bound: float | None,
    range_axis_tick_unit: float | None,
) -> list[tuple[str, str | int]]:
    parameters: list[tuple[str, str | int]] = [
        ("type", "timeSeries"),
        ("width", int(width)),
        ("height", int(height)),
        ("legend", "true"),
        ("dataOrientation", "vertical"),
        ("timeSeries", "true"),
        ("timePeriod", "Day"),
        ("dateFormat", CONFLUENCE_DATE_FORMAT),
        ("domainAxisTickUnit", int(domain_axis_tick_unit)),
        ("domainAxisLabelAngle", "45"),
        ("dateTickMarkPosition", "middle"),
        ("showShapes", "false"),
        ("dataDisplay", "false"),
        ("imageFormat", image_format),
    ]
    if range_axis_lower_bound is not None:
        parameters.append(("rangeAxisLowerBound", _parameter_number(range_axis_lower_bound)))
    if range_axis_upper_bound is not None:
        parameters.append(("rangeAxisUpperBound", _parameter_number(range_axis_upper_bound)))
    if range_axis_tick_unit is not None:
        parameters.append(("rangeAxisTickUnit", _parameter_number(range_axis_tick_unit)))
    if title_placement == "title":
        parameters.insert(0, ("title", title))
    elif title_placement == "subtitle":
        parameters.insert(0, ("subTitle", title))
    elif title_placement != "none":
        raise ValueError(f"Unsupported title_placement: {title_placement}")

    if colors:
        parameters.append(("colors", ",".join(colors)))
    return parameters


def _storage_table_lines(chart_data: _ChartData) -> list[str]:
    lines = [
        '    <table>',
        '      <tbody>',
        '        <tr>',
        '          <th><p>Date</p></th>',
    ]
    lines.extend(f"          <th><p>{escape(series_name)}</p></th>" for series_name in chart_data.series_names)
    lines.append("        </tr>")

    for date in chart_data.dates:
        lines.append("        <tr>")
        lines.append(f"          <td><p>{escape(date)}</p></td>")
        for series_name in chart_data.series_names:
            lines.append(f"          <td><p>{_format_cell_value(chart_data.values_by_date[date].get(series_name))}</p></td>")
        lines.append("        </tr>")

    lines.extend([
        "      </tbody>",
        "    </table>",
    ])
    return lines


def _wiki_table_lines(chart_data: _ChartData) -> list[str]:
    header = "|| Date || " + " || ".join(_escape_wiki_cell(series_name) for series_name in chart_data.series_names) + " ||"
    lines = [header]
    for date in chart_data.dates:
        values = [
            _format_cell_value(chart_data.values_by_date[date].get(series_name))
            for series_name in chart_data.series_names
        ]
        lines.append("| " + _escape_wiki_cell(date) + " | " + " | ".join(values) + " |")
    return lines


def _series_colors(series_names: list[str]) -> list[str]:
    colors = [_hex_color(index) for index, _ in enumerate(series_names)]
    if series_names and series_names[-1] == SIGNIFICANCE_LEVEL_SERIES_NAME:
        colors[-1] = SIGNIFICANCE_LEVEL_COLOR
    return colors


def _parameter_number(value: float) -> str:
    return f"{value:g}"


def _hex_color(index: int) -> str:
    red, green, blue = ECHARTS_COLOR_RGB_VALUES[index % len(ECHARTS_COLOR_RGB_VALUES)]
    return f"#{red:02x}{green:02x}{blue:02x}"


def _domain_axis_tick_unit(dates: list[str], *, max_x_ticks: int) -> int:
    if not dates:
        return 1
    max_ticks = max(1, int(max_x_ticks))
    if max_ticks == 1:
        return max(1, len(dates) + 1)
    return max(1, int(np.ceil(len(dates) / max_ticks)))


def _date_value(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime.datetime | datetime.date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    number_value = float(value)
    if not np.isfinite(number_value):
        return None
    return number_value


def _format_cell_value(value: Any) -> str:
    return format_plain_number(value, default="0")


def _escape_wiki_cell(value: str) -> str:
    return str(value).replace("|", "\\|")
