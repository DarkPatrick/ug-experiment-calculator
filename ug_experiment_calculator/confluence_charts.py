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
SIGNIFICANCE_LEVEL_SERIES_NAME = "p = 0.05"
SIGNIFICANCE_LEVEL_COLOR = "#ff0000"

CONFLUENCE_CHART_COLUMNS: tuple[str, ...] = (
    "dt",
    "variation_pair",
    "pvalue",
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
    height: int = 250,
    include_significance_level: bool = True,
    significance_level: float = 0.05,
    max_x_ticks: int = 2,
    title_placement: Literal["subtitle", "title", "none"] = "none",
    image_format: str = "png",
) -> str:
    chart_data = _prepare_chart_data(
        rows,
        include_significance_level=include_significance_level,
        significance_level=significance_level,
    )
    title = f"Cumulative p-value for {metric} by date"
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
    height: int = 250,
    include_significance_level: bool = True,
    significance_level: float = 0.05,
    max_x_ticks: int = 2,
    title_placement: Literal["subtitle", "title", "none"] = "none",
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
    include_significance_level: bool,
    significance_level: float,
) -> _ChartData:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))

    if df.empty:
        for column in CONFLUENCE_CHART_COLUMNS:
            if column not in df.columns:
                df[column] = pd.Series(dtype="object")
        return _ChartData(dates=[], series_names=[], values_by_date={})

    missing_columns = set(CONFLUENCE_CHART_COLUMNS).difference(df.columns)
    if missing_columns:
        missing_columns_str = ", ".join(sorted(missing_columns))
        raise ValueError(f"Missing Confluence chart columns: {missing_columns_str}")

    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt", "variation_pair"]).copy()
    df["variation_pair"] = df["variation_pair"].astype(str)
    df["pvalue"] = pd.to_numeric(df["pvalue"], errors="coerce")

    sort_columns = ["variation_pair", "dt"]
    if {"control_variation", "test_variation"}.issubset(df.columns):
        sort_columns = ["control_variation", "test_variation", "dt"]
    df = df.sort_values(sort_columns).reset_index(drop=True)
    df["date_value"] = df["dt"].map(_date_value)

    dates = sorted(df["date_value"].dropna().unique().tolist())
    series_names = [str(name) for name in df["variation_pair"].drop_duplicates().tolist()]
    if include_significance_level:
        series_names.append(SIGNIFICANCE_LEVEL_SERIES_NAME)

    values_by_date: dict[str, dict[str, float | None]] = {date: {} for date in dates}
    for _, row in df.iterrows():
        values_by_date[row["date_value"]][row["variation_pair"]] = _number_or_none(row["pvalue"])

    if include_significance_level:
        significance_value = _number_or_none(significance_level)
        for date in dates:
            values_by_date[date][SIGNIFICANCE_LEVEL_SERIES_NAME] = significance_value

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
) -> str:
    parameters = _chart_parameters(
        title=title,
        width=width,
        height=height,
        domain_axis_tick_unit=domain_axis_tick_unit,
        title_placement=title_placement,
        image_format=image_format,
        colors=_series_colors(chart_data.series_names),
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
) -> str:
    parameters = _chart_parameters(
        title=title,
        width=width,
        height=height,
        domain_axis_tick_unit=domain_axis_tick_unit,
        title_placement=title_placement,
        image_format=image_format,
        colors=_series_colors(chart_data.series_names),
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
        ("rangeAxisLowerBound", "0"),
        ("rangeAxisUpperBound", "1"),
        ("rangeAxisTickUnit", "0.25"),
        ("showShapes", "false"),
        ("dataDisplay", "false"),
        ("imageFormat", image_format),
    ]
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
