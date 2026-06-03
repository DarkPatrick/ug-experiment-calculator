from __future__ import annotations

import datetime
import json
from collections.abc import Iterable, Mapping
from typing import Any, Optional

import numpy as np
import pandas as pd

from .config import ExperimentCalculatorConfig


ECHARTS_COLOR_RGB_VALUES: tuple[tuple[int, int, int], ...] = (
    (31, 119, 180),
    (255, 127, 14),
    (44, 160, 44),
    (214, 39, 40),
    (148, 103, 189),
    (140, 86, 75),
    (227, 119, 194),
)

METRIC_RESULT_COLUMNS: tuple[str, ...] = (
    "dt",
    "variation_pair",
    "mean_0",
    "mean_1",
    "mean_diff",
    "lift",
    "ci_low",
    "ci_high",
    "pvalue",
)


def get_metric_echarts_data(
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
            `mean_0`,
            `mean_1`,
            `mean_diff`,
            `lift`,
            `ci_low`,
            `ci_high`,
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


def build_metric_echarts_options(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    df = _prepare_metric_rows(rows)
    grouped_rows = _group_rows_by_variation_pair(df)

    return {
        "lift": _build_lift_option(grouped_rows),
        "confidence_interval": _build_confidence_interval_option(grouped_rows),
    }


def build_metric_echarts_code(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    lift_element_id: str = "metric-lift-chart",
    ci_element_id: str = "metric-ci-chart",
) -> str:
    options = build_metric_echarts_options(rows)
    lift_option_json = json.dumps(options["lift"], ensure_ascii=False, allow_nan=False, indent=2)
    ci_option_json = json.dumps(options["confidence_interval"], ensure_ascii=False, allow_nan=False, indent=2)
    lift_element_id_json = json.dumps(lift_element_id, ensure_ascii=False)
    ci_element_id_json = json.dumps(ci_element_id, ensure_ascii=False)

    return f"""(function () {{
  function formatNice(value, precision) {{
    if (value === null || value === undefined || value === '') return value;
    var numberValue = Number(value);
    if (!Number.isFinite(numberValue)) return value;
    return numberValue.toPrecision(precision);
  }}

  function normalizeParams(params) {{
    return Array.isArray(params) ? params : [params];
  }}

  function axisDate(params) {{
    for (var i = 0; i < params.length; i++) {{
      if (params[i].axisValueLabel) return params[i].axisValueLabel;
    }}
    for (var j = 0; j < params.length; j++) {{
      var value = params[j].value;
      if (!Array.isArray(value)) continue;
      if (typeof value[0] === 'string') return value[0];
      if (typeof value[1] === 'string') return value[1];
    }}
    return '';
  }}

  var liftOption = {lift_option_json};
  liftOption.tooltip.formatter = function (params) {{
    var normalizedParams = normalizeParams(params);
    params = normalizedParams.filter(function (param) {{
      return param.data && param.data.tooltipRole === 'lift';
    }});

    var lines = ['date: ' + axisDate(normalizedParams)];
    params.forEach(function (param) {{
      var data = param.data;
      lines.push(param.marker + param.seriesName);
      lines.push('control: ' + formatNice(data.control, 4));
      lines.push('test: ' + formatNice(data.test, 4));
      lines.push('diff: ' + formatNice(data.diff, 4));
      lines.push('lift: ' + formatNice(data.lift, 4) + '%');
    }});
    return lines.join('<br>');
  }};

  var ciOption = {ci_option_json};
  ciOption.tooltip.formatter = function (params) {{
    var normalizedParams = normalizeParams(params);
    params = normalizedParams.filter(function (param) {{
      return param.data && param.data.tooltipRole === 'ciBand';
    }});

    var lines = ['date: ' + axisDate(normalizedParams)];
    params.forEach(function (param) {{
      var data = param.data;
      lines.push(param.marker + param.seriesName);
      lines.push('CI: [' + formatNice(data.ciLow, 3) + ', ' + formatNice(data.ciHigh, 3) + ']');
      lines.push('p-value: ' + formatNice(data.pvalue, 3));
    }});
    return lines.join('<br>');
  }};

  var liftElement = document.getElementById({lift_element_id_json});
  if (liftElement) {{
    echarts.init(liftElement).setOption(liftOption);
  }}

  var ciElement = document.getElementById({ci_element_id_json});
  if (ciElement) {{
    echarts.init(ciElement).setOption(ciOption);
  }}
}})();"""


def get_metric_echarts_code(
    exp_id: int,
    metric: str,
    client: str,
    segment: str,
    *,
    lift_element_id: str = "metric-lift-chart",
    ci_element_id: str = "metric-ci-chart",
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    rows = get_metric_echarts_data(exp_id, metric, client, segment, config=config)
    return build_metric_echarts_code(rows, lift_element_id=lift_element_id, ci_element_id=ci_element_id)


def _prepare_metric_rows(rows: pd.DataFrame | Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))

    if df.empty:
        for column in METRIC_RESULT_COLUMNS:
            if column not in df.columns:
                df[column] = pd.Series(dtype="object")
        return df

    missing_columns = set(METRIC_RESULT_COLUMNS).difference(df.columns)
    if missing_columns:
        missing_columns_str = ", ".join(sorted(missing_columns))
        raise ValueError(f"Missing metric result columns: {missing_columns_str}")

    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt", "variation_pair"]).copy()
    df["variation_pair"] = df["variation_pair"].astype(str)

    for column in ("mean_0", "mean_1", "mean_diff", "lift", "ci_low", "ci_high", "pvalue"):
        df[column] = pd.to_numeric(df[column], errors="coerce")

    sort_columns = ["variation_pair", "dt"]
    if {"control_variation", "test_variation"}.issubset(df.columns):
        sort_columns = ["control_variation", "test_variation", "dt"]

    return df.sort_values(sort_columns).reset_index(drop=True)


def _group_rows_by_variation_pair(df: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    return [(str(variation_pair), group.copy()) for variation_pair, group in df.groupby("variation_pair", sort=False)]


def _build_lift_option(grouped_rows: list[tuple[str, pd.DataFrame]]) -> dict[str, Any]:
    series = []
    legend_data = [variation_pair for variation_pair, _ in grouped_rows]
    for index, (variation_pair, group) in enumerate(grouped_rows):
        color = _rgb_color(index)
        series.append({
            "name": variation_pair,
            "type": "line",
            "showSymbol": False,
            "connectNulls": False,
            "data": [
                {
                    "value": [_date_value(row["dt"]), _number_or_none(row["lift"])],
                    "control": _number_or_none(row["mean_0"]),
                    "test": _number_or_none(row["mean_1"]),
                    "diff": _number_or_none(row["mean_diff"]),
                    "lift": _number_or_none(row["lift"]),
                    "tooltipRole": "lift",
                }
                for _, row in group.sort_values("dt").iterrows()
            ],
            "lineStyle": {"width": 2, "color": color},
            "itemStyle": {"color": color},
        })

    return {
        "color": [_rgb_color(index) for index in range(len(grouped_rows))],
        "tooltip": {"trigger": "axis"},
        "legend": {"type": "scroll", "top": 0, "data": legend_data},
        "grid": {"left": 60, "right": 30, "top": 45, "bottom": 60, "containLabel": True},
        "xAxis": {"type": "time"},
        "yAxis": {"type": "value", "name": "lift, %"},
        "series": series,
    }


def _build_confidence_interval_option(grouped_rows: list[tuple[str, pd.DataFrame]]) -> dict[str, Any]:
    series = []
    legend_data = [variation_pair for variation_pair, _ in grouped_rows]
    for index, (variation_pair, group) in enumerate(grouped_rows):
        color = _rgb_color(index)
        fill_color = _rgba_color(index, 0.15)
        sorted_group = group.sort_values("dt")

        band_high_area_series = {
            "name": variation_pair,
            "type": "line",
            "showSymbol": False,
            "connectNulls": False,
            "data": [
                {
                    "value": [_date_value(row["dt"]), _number_or_none(row["ci_high"])],
                    "ciLow": _number_or_none(row["ci_low"]),
                    "ciHigh": _number_or_none(row["ci_high"]),
                    "pvalue": _number_or_none(row["pvalue"]),
                    "tooltipRole": "ciBand",
                }
                for _, row in sorted_group.iterrows()
            ],
            "areaStyle": {"color": fill_color, "origin": "start"},
            "lineStyle": {"width": 0, "opacity": 0},
            "itemStyle": {"color": fill_color},
            "z": 1,
        }
        if not series:
            band_high_area_series["markLine"] = _zero_mark_line()

        band_low_mask_series = {
            "name": variation_pair,
            "type": "line",
            "showSymbol": False,
            "connectNulls": False,
            "data": [
                {
                    "value": [_date_value(row["dt"]), _number_or_none(row["ci_low"])],
                    "tooltipRole": "ciBandMask",
                }
                for _, row in sorted_group.iterrows()
            ],
            "areaStyle": {"color": "rgba(255, 255, 255, 1)", "origin": "start"},
            "lineStyle": {"width": 0, "opacity": 0},
            "itemStyle": {"color": "rgba(255, 255, 255, 1)"},
            "tooltip": {"show": False},
            "z": 2,
        }

        low_series = {
            "name": variation_pair,
            "type": "line",
            "showSymbol": False,
            "connectNulls": False,
            "data": [
                {
                    "value": [_date_value(row["dt"]), _number_or_none(row["ci_low"])],
                    "tooltipRole": "ciLow",
                }
                for _, row in sorted_group.iterrows()
            ],
            "lineStyle": {"width": 2, "color": color},
            "itemStyle": {"color": color},
            "tooltip": {"show": False},
            "z": 3,
        }

        high_series = {
            "name": variation_pair,
            "type": "line",
            "showSymbol": False,
            "connectNulls": False,
            "data": [
                {
                    "value": [_date_value(row["dt"]), _number_or_none(row["ci_high"])],
                    "tooltipRole": "ciHigh",
                }
                for _, row in sorted_group.iterrows()
            ],
            "lineStyle": {"width": 2, "color": color},
            "itemStyle": {"color": color},
            "tooltip": {"show": False},
            "z": 3,
        }

        series.extend([band_high_area_series, band_low_mask_series, low_series, high_series])

    return {
        "color": [_rgb_color(index) for index in range(len(grouped_rows))],
        "tooltip": {"trigger": "axis"},
        "legend": {"type": "scroll", "top": 0, "data": legend_data},
        "grid": {"left": 60, "right": 30, "top": 45, "bottom": 60, "containLabel": True},
        "xAxis": {"type": "time"},
        "yAxis": {"type": "value", "name": "confidence intervals"},
        "series": series,
    }


def _zero_mark_line() -> dict[str, Any]:
    return {
        "silent": True,
        "symbol": "none",
        "label": {"show": False},
        "lineStyle": {"color": "red", "width": 3, "type": "dashed"},
        "data": [{"yAxis": 0}],
    }


def _rgb_color(index: int) -> str:
    red, green, blue = ECHARTS_COLOR_RGB_VALUES[index % len(ECHARTS_COLOR_RGB_VALUES)]
    return f"rgb({red}, {green}, {blue})"


def _rgba_color(index: int, alpha: float) -> str:
    red, green, blue = ECHARTS_COLOR_RGB_VALUES[index % len(ECHARTS_COLOR_RGB_VALUES)]
    return f"rgba({red}, {green}, {blue}, {alpha})"


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
