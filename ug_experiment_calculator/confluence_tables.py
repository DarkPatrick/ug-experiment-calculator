from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
import uuid
from typing import Any, Optional

import numpy as np
import pandas as pd

from .config import ExperimentCalculatorConfig
from .confluence_charts import build_metric_confluence_chart_code
from .metrics import load_metrics_config, normalize_metric_config


HEADER_COLOR = "#eae6ff"
PVALUE_NEUTRAL_COLOR = "#fffae6"
PVALUE_POSITIVE_COLOR = "#e3fcef"
PVALUE_NEGATIVE_COLOR = "#ffebe6"
TOTAL_SEGMENT = "Total"

TABLE_COLUMNS: tuple[str, ...] = (
    "dt",
    "metric",
    "variation_pair",
    "mean_0",
    "mean_1",
    "lift",
    "pvalue",
    "client",
    "segment",
)


@dataclass(frozen=True)
class _MetricTableConfig:
    name: str
    table_position: int
    positive: bool
    source_index: int


def get_experiment_confluence_table_data(
    exp_id: int,
    *,
    clients: Optional[Sequence[str]] = None,
    segments: Optional[Sequence[str]] = None,
    metrics: Optional[Sequence[str]] = None,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    from clickhouse_worker import clickhouse_string_literal as _clickhouse_string_literal
    from clickhouse_worker import execute_sql

    cfg = config or ExperimentCalculatorConfig.from_env()
    filters = [f"`exp_id` = {int(exp_id)}"]
    filters.extend(_in_filter("client", clients, _clickhouse_string_literal))
    filters.extend(_in_filter("segment", segments, _clickhouse_string_literal))
    filters.extend(_in_filter("metric", metrics, _clickhouse_string_literal))
    where_sql = "\n        and ".join(filters)

    query = f"""
        select
            `dt`,
            `metric`,
            `variation_pair`,
            `control_variation`,
            `test_variation`,
            `mean_0`,
            `mean_1`,
            `lift`,
            `pvalue`,
            `client`,
            `segment`
        from {cfg.exp_results_table}
        where
            {where_sql}
        order by
            `client`,
            `segment`,
            `dt`,
            `control_variation`,
            `test_variation`,
            `metric`
    """
    return execute_sql(query)


def get_experiment_confluence_table_code(
    exp_id: int,
    *,
    clients: Optional[Sequence[str]] = None,
    segments: Optional[Sequence[str]] = None,
    metrics_yaml_path: str | Path | None = None,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    cfg = config or ExperimentCalculatorConfig.from_env()
    metric_configs = _load_metric_table_configs(metrics_yaml_path or cfg.metrics_yaml_path)
    rows = get_experiment_confluence_table_data(
        exp_id,
        clients=clients,
        segments=segments,
        metrics=[metric_config.name for metric_config in metric_configs],
        config=cfg,
    )
    return build_experiment_confluence_table_code(
        rows,
        metrics_yaml_path=metrics_yaml_path or cfg.metrics_yaml_path,
    )


def build_experiment_confluence_table_code(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    metrics_yaml_path: str | Path | None = None,
) -> str:
    df = _prepare_table_rows(rows)
    cfg = ExperimentCalculatorConfig.from_env()
    metric_configs = _load_metric_table_configs(metrics_yaml_path or cfg.metrics_yaml_path)

    if df.empty or not metric_configs:
        return _table([])

    client_names = _ordered_values(df["client"])
    client_blocks = []
    for client in client_names:
        client_df = df[df["client"] == client].copy()
        table_html = _build_client_table(client_df, metric_configs)
        if not table_html:
            continue
        if len(client_names) > 1:
            client_blocks.append(_ui_expand(str(client), table_html))
        else:
            client_blocks.append(table_html)

    return "\n".join(client_blocks)


def _build_client_table(df: pd.DataFrame, metric_configs: list[_MetricTableConfig]) -> str:
    metric_names = set(df["metric"])
    metric_configs = [metric_config for metric_config in metric_configs if metric_config.name in metric_names]
    if not metric_configs:
        return ""

    rows = []
    segment_names = _ordered_segments(df["segment"])
    for segment_index, segment in enumerate(segment_names):
        segment_df = df[df["segment"] == segment].copy()
        if segment_index > 0 or segment != TOTAL_SEGMENT:
            rows.append(_segment_row(str(segment), len(metric_configs) + 1))
        rows.extend(_build_segment_rows(segment_df, metric_configs))
    return _table(rows)


def _build_segment_rows(df: pd.DataFrame, metric_configs: list[_MetricTableConfig]) -> list[str]:
    latest_df = _latest_rows(df)
    test_variations = _test_variations(latest_df)

    rows = [_header_row(metric_configs)]
    rows.append(_control_row(latest_df, metric_configs))

    for test_variation in test_variations:
        rows.append(_test_variation_row(latest_df, metric_configs, test_variation))
        rows.append(_lift_row(latest_df, metric_configs, test_variation))
        rows.append(_pvalue_row(latest_df, metric_configs, test_variation))

    rows.append(_cumulatives_row(df, metric_configs))
    return rows


def _header_row(metric_configs: list[_MetricTableConfig]) -> str:
    cells = [_cell("Variation", background=HEADER_COLOR, bold=True)]
    cells.extend(_cell(metric_config.name, background=HEADER_COLOR, bold=True) for metric_config in metric_configs)
    return _row(cells)


def _control_row(df: pd.DataFrame, metric_configs: list[_MetricTableConfig]) -> str:
    cells = [_row_header_cell("Control")]
    for metric_config in metric_configs:
        metric_rows = df[df["metric"] == metric_config.name]
        cells.append(_cell(_format_number(_first_number(metric_rows, "mean_0"))))
    return _row(cells)


def _test_variation_row(
    df: pd.DataFrame,
    metric_configs: list[_MetricTableConfig],
    test_variation: Any,
) -> str:
    cells = [_row_header_cell(f"Variation {_format_variation(test_variation)}")]
    for metric_config in metric_configs:
        row = _latest_metric_pair_row(df, metric_config.name, test_variation)
        cells.append(_cell(_format_number(_row_number(row, "mean_1"))))
    return _row(cells)


def _lift_row(
    df: pd.DataFrame,
    metric_configs: list[_MetricTableConfig],
    test_variation: Any,
) -> str:
    cells = [_row_header_cell("diff, %")]
    for metric_config in metric_configs:
        row = _latest_metric_pair_row(df, metric_config.name, test_variation)
        cells.append(_cell(_format_number(_row_number(row, "lift"))))
    return _row(cells)


def _pvalue_row(
    df: pd.DataFrame,
    metric_configs: list[_MetricTableConfig],
    test_variation: Any,
) -> str:
    cells = [_row_header_cell("pvalue")]
    for metric_config in metric_configs:
        row = _latest_metric_pair_row(df, metric_config.name, test_variation)
        pvalue = _row_number(row, "pvalue")
        lift = _row_number(row, "lift")
        cells.append(_cell(_format_number(pvalue), background=_pvalue_background(pvalue, lift, metric_config.positive)))
    return _row(cells)


def _cumulatives_row(df: pd.DataFrame, metric_configs: list[_MetricTableConfig]) -> str:
    cells = [_row_header_cell("cumulatives")]
    for metric_config in metric_configs:
        metric_rows = df[df["metric"] == metric_config.name]
        if metric_rows.empty:
            cells.append(_cell(""))
            continue

        chart_code = build_metric_confluence_chart_code(metric_rows, metric_config.name)
        cells.append(_cell(chart_code, raw=True))
    return _row(cells)


def _load_metric_table_configs(metrics_yaml_path: str | Path) -> list[_MetricTableConfig]:
    metrics_config = load_metrics_config(metrics_yaml_path)
    result = []
    for metric_index, (metric_name, metric_items) in enumerate(metrics_config.items()):
        metric_config = normalize_metric_config(metric_items)
        table_position = int(metric_config.get("table_position") or 0)
        if table_position <= 0:
            continue

        result.append(_MetricTableConfig(
            name=metric_name,
            table_position=table_position,
            positive=bool(int(metric_config.get("positive", 1))),
            source_index=metric_index,
        ))

    return sorted(result, key=lambda item: (item.table_position, item.source_index, item.name))


def _prepare_table_rows(rows: pd.DataFrame | Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    if df.empty:
        for column in TABLE_COLUMNS:
            if column not in df.columns:
                df[column] = pd.Series(dtype="object")
        return df

    missing_columns = set(TABLE_COLUMNS).difference(df.columns)
    if missing_columns:
        missing_columns_str = ", ".join(sorted(missing_columns))
        raise ValueError(f"Missing Confluence table columns: {missing_columns_str}")

    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt", "metric", "variation_pair", "client", "segment"]).copy()

    for column in ("metric", "variation_pair", "client", "segment"):
        df[column] = df[column].astype(str)
    for column in ("mean_0", "mean_1", "lift", "pvalue"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    if "test_variation" not in df.columns:
        df["test_variation"] = df["variation_pair"].map(_parse_test_variation)
    else:
        df["test_variation"] = df["test_variation"].map(_normalize_variation_value)

    sort_columns = ["client", "segment", "metric", "dt"]
    return df.sort_values(sort_columns).reset_index(drop=True)


def _latest_rows(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values("dt")
        .groupby(["metric", "variation_pair"], as_index=False, dropna=False)
        .tail(1)
        .reset_index(drop=True)
    )


def _latest_metric_pair_row(df: pd.DataFrame, metric: str, test_variation: Any) -> pd.Series | None:
    row = df[(df["metric"] == metric) & (df["test_variation"].map(str) == str(test_variation))]
    if row.empty:
        return None
    return row.iloc[0]


def _test_variations(df: pd.DataFrame) -> list[Any]:
    values = df["test_variation"].dropna().drop_duplicates().tolist()
    return sorted(values, key=_variation_sort_key)


def _ordered_values(series: pd.Series) -> list[Any]:
    return series.dropna().drop_duplicates().tolist()


def _ordered_segments(series: pd.Series) -> list[Any]:
    values = _ordered_values(series)
    total_values = [value for value in values if value == TOTAL_SEGMENT]
    other_values = [value for value in values if value != TOTAL_SEGMENT]
    return total_values + other_values


def _pvalue_background(pvalue: float | None, lift: float | None, positive: bool) -> str | None:
    if pvalue is None:
        return None
    if pvalue >= 0.05 or lift is None or lift == 0:
        return PVALUE_NEUTRAL_COLOR
    is_good = lift > 0 if positive else lift < 0
    return PVALUE_POSITIVE_COLOR if is_good else PVALUE_NEGATIVE_COLOR


def _first_number(df: pd.DataFrame, column: str) -> float | None:
    if df.empty:
        return None
    return _number_or_none(df.iloc[0][column])


def _row_number(row: pd.Series | None, column: str) -> float | None:
    if row is None:
        return None
    return _number_or_none(row[column])


def _number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    number_value = float(value)
    if not np.isfinite(number_value):
        return None
    return number_value


def _format_number(value: Any) -> str:
    number_value = _number_or_none(value)
    if number_value is None:
        return ""
    return f"{number_value:.4g}"


def _format_variation(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _parse_test_variation(variation_pair: str) -> Any:
    parts = str(variation_pair).split(" vs ")
    if len(parts) != 2:
        return variation_pair
    return _normalize_variation_value(parts[1])


def _normalize_variation_value(value: Any) -> Any:
    number_value = _number_or_none(value)
    if number_value is None:
        return value
    if number_value.is_integer():
        return int(number_value)
    return number_value


def _variation_sort_key(value: Any) -> tuple[int, Any]:
    number_value = _number_or_none(value)
    if number_value is None:
        return (1, str(value))
    return (0, number_value)


def _table(rows: list[str]) -> str:
    return "\n".join([
        "<table>",
        "  <tbody>",
        *[f"    {row}" for row in rows],
        "  </tbody>",
        "</table>",
    ])


def _row(cells: list[str]) -> str:
    return "<tr>" + "".join(cells) + "</tr>"


def _row_header_cell(value: str) -> str:
    return _cell(value, background=HEADER_COLOR, bold=True)


def _segment_row(segment: str, colspan: int) -> str:
    return _row([_cell(segment, background=HEADER_COLOR, bold=True, colspan=colspan)])


def _cell(
    value: str,
    *,
    background: str | None = None,
    bold: bool = False,
    colspan: int | None = None,
    raw: bool = False,
) -> str:
    attributes = []
    if background:
        attributes.append(f'data-highlight-colour="{escape(background)}"')
    if colspan is not None:
        attributes.append(f'colspan="{int(colspan)}"')
    attributes_str = " " + " ".join(attributes) if attributes else ""

    if raw:
        return f"<td{attributes_str}>{value}</td>"

    escaped_value = escape(str(value))
    if bold:
        escaped_value = f"<strong>{escaped_value}</strong>"
    return f"<td{attributes_str}><p>{escaped_value}</p></td>"


def _ui_expand(title: str, body: str) -> str:
    macro_id = str(uuid.uuid4())
    return "\n".join([
        f'<ac:structured-macro ac:name="ui-expand" ac:macro-id="{macro_id}">',
        f'  <ac:parameter ac:name="title">{escape(title)}</ac:parameter>',
        "  <ac:rich-text-body>",
        body,
        "  </ac:rich-text-body>",
        "</ac:structured-macro>",
    ])


def _in_filter(column: str, values: Optional[Sequence[str]], literal_func) -> list[str]:
    if not values:
        return []
    quoted_values = ", ".join(literal_func(value) for value in values)
    return [f"`{column}` in ({quoted_values})"]
