from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .colors import (
    PVALUE_NEGATIVE_COLOR,
    PVALUE_NEUTRAL_COLOR,
    PVALUE_POSITIVE_COLOR,
)
from .config import ExperimentCalculatorConfig
from .metrics import load_metrics_config, normalize_metric_config
from .value_formatting import (
    format_diff_percent,
    format_metric_value,
    format_pvalue,
    number_or_none,
)


LATEST_RESULTS_COLUMNS: tuple[str, ...] = (
    "dt",
    "client",
    "segment",
    "variation_pair",
    "metric",
    "mean_0",
    "mean_1",
    "mean_diff",
    "lift",
    "ci_low",
    "ci_high",
    "pvalue",
)

LATEST_STATS_COLUMNS: tuple[str, ...] = (
    "dt",
    "client",
    "segment",
    "metric",
    "variation",
    "value",
)


@dataclass(frozen=True)
class _MetricFormatConfig:
    prefix: str = ""
    suffix: str = ""
    positive: bool = True


def get_latest_experiment_summary_tables(
    exp_id: int,
    *,
    clients: Optional[Sequence[str]] = None,
    segments: Optional[Sequence[str]] = None,
    metrics: Optional[Sequence[str]] = None,
    metrics_yaml_path: str | Path | None = None,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = config or ExperimentCalculatorConfig.from_env()
    results_rows = _get_latest_results_rows(
        exp_id,
        clients=clients,
        segments=segments,
        metrics=metrics,
        config=cfg,
    )
    stats_rows = _get_latest_stats_rows(
        exp_id,
        clients=clients,
        segments=segments,
        metrics=metrics,
        config=cfg,
    )
    return build_latest_experiment_summary_tables(
        results_rows,
        stats_rows,
        metrics_yaml_path=metrics_yaml_path or cfg.metrics_yaml_path,
    )


def build_latest_experiment_summary_tables(
    results_rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    stats_rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    metrics_yaml_path: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = ExperimentCalculatorConfig.from_env()
    metric_configs = _load_metric_format_configs(metrics_yaml_path or cfg.metrics_yaml_path)
    results_df = _build_results_summary_table(results_rows, metric_configs)
    stats_df = _build_stats_summary_table(stats_rows, metric_configs)
    return results_df, stats_df


def _get_latest_results_rows(
    exp_id: int,
    *,
    clients: Optional[Sequence[str]],
    segments: Optional[Sequence[str]],
    metrics: Optional[Sequence[str]],
    config: ExperimentCalculatorConfig,
) -> pd.DataFrame:
    from clickhouse_worker import clickhouse_string_literal as _clickhouse_string_literal
    from clickhouse_worker import execute_sql

    filters = [f"`exp_id` = {int(exp_id)}"]
    filters.extend(_in_filter("client", clients, _clickhouse_string_literal))
    filters.extend(_in_filter("segment", segments, _clickhouse_string_literal))
    filters.extend(_in_filter("metric", metrics, _clickhouse_string_literal))
    where_sql = "\n        and ".join(filters)

    query = f"""
        select
            `dt`,
            `client`,
            `segment`,
            `variation_pair`,
            `metric`,
            `mean_0`,
            `mean_1`,
            `mean_diff`,
            `lift`,
            `ci_low`,
            `ci_high`,
            `pvalue`
        from {config.exp_results_table}
        where
            {where_sql}
        and `dt` = (
            select max(`dt`)
            from {config.exp_results_table}
            where
                {where_sql}
        )
        order by
            `client`,
            `segment`,
            `metric`,
            `variation_pair`
    """
    return execute_sql(query)


def _get_latest_stats_rows(
    exp_id: int,
    *,
    clients: Optional[Sequence[str]],
    segments: Optional[Sequence[str]],
    metrics: Optional[Sequence[str]],
    config: ExperimentCalculatorConfig,
) -> pd.DataFrame:
    from clickhouse_worker import clickhouse_string_literal as _clickhouse_string_literal
    from clickhouse_worker import execute_sql

    filters = [f"`exp_id` = {int(exp_id)}"]
    filters.extend(_in_filter("client", clients, _clickhouse_string_literal))
    filters.extend(_in_filter("segment", segments, _clickhouse_string_literal))
    filters.extend(_in_filter("metric", metrics, _clickhouse_string_literal))
    where_sql = "\n        and ".join(filters)

    query = f"""
        select
            `dt`,
            `client`,
            `segment`,
            `metric`,
            `variation`,
            `value`
        from {config.exp_stats_table}
        where
            {where_sql}
        and `dt` = (
            select max(`dt`)
            from {config.exp_stats_table}
            where
                {where_sql}
        )
        order by
            `client`,
            `segment`,
            `metric`,
            `variation`
    """
    return execute_sql(query)


def _build_results_summary_table(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    metric_configs: dict[str, _MetricFormatConfig],
) -> pd.DataFrame:
    df = _latest_date_rows(_prepare_rows(rows, LATEST_RESULTS_COLUMNS), ["client", "segment", "metric", "variation_pair"])
    columns = ["client", "segment", "variation_pair", "metric", "control", "test", "diff", "diff, %", "ci_low", "ci_high", "pvalue", "color"]
    if df.empty:
        return _empty_string_df(columns)

    result = pd.DataFrame({
        "client": df["client"].map(_format_text),
        "segment": df["segment"].map(_format_text),
        "variation_pair": df["variation_pair"].map(_format_text),
        "metric": df["metric"].map(_format_text),
        "control": [
            _format_metric_column_value(row["mean_0"], row["metric"], metric_configs)
            for _, row in df.iterrows()
        ],
        "test": [
            _format_metric_column_value(row["mean_1"], row["metric"], metric_configs)
            for _, row in df.iterrows()
        ],
        "diff": [
            _format_metric_column_value(row["mean_diff"], row["metric"], metric_configs)
            for _, row in df.iterrows()
        ],
        "diff, %": df["lift"].map(format_diff_percent),
        "ci_low": [
            _format_metric_column_value(row["ci_low"], row["metric"], metric_configs)
            for _, row in df.iterrows()
        ],
        "ci_high": [
            _format_metric_column_value(row["ci_high"], row["metric"], metric_configs)
            for _, row in df.iterrows()
        ],
        "pvalue": df["pvalue"].map(format_pvalue),
        "color": [
            _pvalue_background(row["pvalue"], row["lift"], row["metric"], metric_configs)
            for _, row in df.iterrows()
        ],
    })
    return _string_df(result[columns])


def _build_stats_summary_table(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    metric_configs: dict[str, _MetricFormatConfig],
) -> pd.DataFrame:
    df = _latest_date_rows(_prepare_rows(rows, LATEST_STATS_COLUMNS), ["client", "segment", "metric", "variation"])
    columns = ["client", "segment", "metric", "variation", "value"]
    if df.empty:
        return _empty_string_df(columns)

    result = pd.DataFrame({
        "client": df["client"].map(_format_text),
        "segment": df["segment"].map(_format_text),
        "metric": df["metric"].map(_format_text),
        "variation": df["variation"].map(_format_variation),
        "value": [
            _format_metric_column_value(row["value"], row["metric"], metric_configs)
            for _, row in df.iterrows()
        ],
    })
    return _string_df(result[columns])


def _prepare_rows(rows: pd.DataFrame | Iterable[Mapping[str, Any]], required_columns: tuple[str, ...]) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    if df.empty:
        for column in required_columns:
            if column not in df.columns:
                df[column] = pd.Series(dtype="object")
        return df

    missing_columns = set(required_columns).difference(df.columns)
    if missing_columns:
        missing_columns_str = ", ".join(sorted(missing_columns))
        raise ValueError(f"Missing summary table columns: {missing_columns_str}")

    df = df.copy()
    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt", "client", "segment", "metric"]).copy()
    for column in ("client", "segment", "metric"):
        df[column] = df[column].astype(str)
    return df


def _latest_date_rows(df: pd.DataFrame, sort_columns: list[str]) -> pd.DataFrame:
    if df.empty:
        return df
    latest_dt = df["dt"].max()
    return df[df["dt"] == latest_dt].copy().sort_values(sort_columns).reset_index(drop=True)


def _load_metric_format_configs(metrics_yaml_path: str | Path) -> dict[str, _MetricFormatConfig]:
    metrics_config = load_metrics_config(metrics_yaml_path)
    result = {}
    for metric_name, metric_items in metrics_config.items():
        metric_config = normalize_metric_config(metric_items)
        result[str(metric_name)] = _MetricFormatConfig(
            prefix=str(metric_config.get("prefix") or ""),
            suffix=str(metric_config.get("suffix") or ""),
            positive=bool(int(metric_config.get("positive", 1))),
        )
    return result


def _format_metric_column_value(value: Any, metric: str, metric_configs: dict[str, _MetricFormatConfig]) -> str:
    metric_config = metric_configs.get(str(metric), _MetricFormatConfig())
    return format_metric_value(value, prefix=metric_config.prefix, suffix=metric_config.suffix)


def _pvalue_background(
    pvalue: Any,
    diff_percent: Any,
    metric: str,
    metric_configs: dict[str, _MetricFormatConfig],
) -> str:
    pvalue_number = number_or_none(pvalue)
    diff_percent_number = number_or_none(diff_percent)
    if pvalue_number is None:
        return ""
    if pvalue_number >= 0.05 or diff_percent_number is None or diff_percent_number == 0:
        return PVALUE_NEUTRAL_COLOR

    metric_config = metric_configs.get(str(metric), _MetricFormatConfig())
    is_good = diff_percent_number > 0 if metric_config.positive else diff_percent_number < 0
    return PVALUE_POSITIVE_COLOR if is_good else PVALUE_NEGATIVE_COLOR


def _format_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def _format_variation(value: Any) -> str:
    number_value = number_or_none(value)
    if number_value is not None and number_value.is_integer():
        return str(int(number_value))
    return _format_text(value)


def _string_df(df: pd.DataFrame) -> pd.DataFrame:
    return df.fillna("").astype(str)


def _empty_string_df(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="object") for column in columns}).astype(str)


def _in_filter(column: str, values: Optional[Sequence[str]], literal_func) -> list[str]:
    if not values:
        return []
    quoted_values = ", ".join(literal_func(value) for value in values)
    return [f"`{column}` in ({quoted_values})"]
