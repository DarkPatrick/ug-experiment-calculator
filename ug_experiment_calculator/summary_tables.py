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
from .metrics import (
    config_enabled_for_domain,
    config_enabled_for_subdomain,
    config_included_in_summary,
    load_metrics_config,
    normalize_metric_config,
)
from .value_formatting import (
    apply_number_affixes,
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
class _SummaryItemConfig:
    source_name: str
    display_name: str
    description: str
    table_position: int
    sources: tuple[str, ...]
    prefix: str = ""
    suffix: str = ""
    value_type: str = ""
    positive: bool = True


def get_latest_experiment_summary_tables(
    exp_id: int,
    *,
    clients: Optional[Sequence[str]] = None,
    segments: Optional[Sequence[str]] = None,
    metrics: Optional[Sequence[str]] = None,
    metrics_yaml_path: str | Path | None = None,
    stats_yaml_path: str | Path | None = None,
    domain: str = "monetization",
    subdomain: str | None = None,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = config or ExperimentCalculatorConfig.from_env()
    metric_configs = _load_summary_item_configs(metrics_yaml_path or cfg.metrics_yaml_path, domain=domain, subdomain=subdomain)
    stats_configs = _load_summary_item_configs(stats_yaml_path or cfg.stats_yaml_path, domain=domain, subdomain=subdomain)
    results_rows = _get_latest_results_rows(
        exp_id,
        clients=clients,
        segments=segments,
        metrics=_filtered_config_names(metric_configs, metrics),
        config=cfg,
    )
    stats_rows = _get_latest_stats_rows(
        exp_id,
        clients=clients,
        segments=segments,
        metrics=_filtered_config_names(stats_configs, metrics),
        config=cfg,
    )
    return build_latest_experiment_summary_tables(
        results_rows,
        stats_rows,
        metrics_yaml_path=metrics_yaml_path or cfg.metrics_yaml_path,
        stats_yaml_path=stats_yaml_path or cfg.stats_yaml_path,
        domain=domain,
        subdomain=subdomain,
    )


def build_latest_experiment_summary_tables(
    results_rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    stats_rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    metrics_yaml_path: str | Path | None = None,
    stats_yaml_path: str | Path | None = None,
    domain: str = "monetization",
    subdomain: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = ExperimentCalculatorConfig.from_env()
    metric_configs = _load_summary_item_configs(metrics_yaml_path or cfg.metrics_yaml_path, domain=domain, subdomain=subdomain)
    stats_configs = _load_summary_item_configs(stats_yaml_path or cfg.stats_yaml_path, domain=domain, subdomain=subdomain)
    results_df = _build_results_summary_table(results_rows, metric_configs)
    stats_df = _build_stats_summary_table(stats_rows, stats_configs)
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
    metric_configs: dict[str, _SummaryItemConfig],
) -> pd.DataFrame:
    df = _latest_date_rows(_prepare_rows(rows, LATEST_RESULTS_COLUMNS))
    df = _filter_configured_rows(df, metric_configs)
    df = _sort_summary_rows(df, ["variation_pair"])
    columns = [
        "client",
        "segment",
        "variation_pair",
        "metric",
        "description",
        "control",
        "test",
        "diff",
        "diff, %",
        "ci_low",
        "ci_high",
        "pvalue",
        "color",
    ]
    if df.empty:
        return _empty_string_df(columns)

    result = pd.DataFrame({
        "client": df["client"].map(_format_text),
        "segment": df["segment"].map(_format_text),
        "variation_pair": df["variation_pair"].map(_format_text),
        "metric": [
            _summary_config(row["metric"], metric_configs).display_name
            for _, row in df.iterrows()
        ],
        "description": [
            _summary_config(row["metric"], metric_configs).description
            for _, row in df.iterrows()
        ],
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
    stats_configs: dict[str, _SummaryItemConfig],
) -> pd.DataFrame:
    df = _latest_date_rows(_prepare_rows(rows, LATEST_STATS_COLUMNS))
    df = _filter_configured_rows(df, stats_configs)
    df = _sort_summary_rows(df, ["variation"])
    columns = ["client", "segment", "metric", "description", "variation", "value"]
    if df.empty:
        return _empty_string_df(columns)

    result = pd.DataFrame({
        "client": df["client"].map(_format_text),
        "segment": df["segment"].map(_format_text),
        "metric": [
            _summary_config(row["metric"], stats_configs).display_name
            for _, row in df.iterrows()
        ],
        "description": [
            _summary_config(row["metric"], stats_configs).description
            for _, row in df.iterrows()
        ],
        "variation": df["variation"].map(_format_variation),
        "value": [
            _format_metric_column_value(row["value"], row["metric"], stats_configs)
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


def _latest_date_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    latest_dt = df["dt"].max()
    return df[df["dt"] == latest_dt].copy().reset_index(drop=True)


def _load_summary_item_configs(
    yaml_path: str | Path,
    *,
    domain: str | None = None,
    subdomain: str | None = None,
) -> dict[str, _SummaryItemConfig]:
    raw_config = load_metrics_config(yaml_path)
    result = {}
    for source_index, (item_name, item_config_items) in enumerate(raw_config.items()):
        item_config = normalize_metric_config(item_config_items)
        if not config_enabled_for_domain(item_config, domain):
            continue
        if not config_enabled_for_subdomain(item_config, subdomain):
            continue
        if not config_included_in_summary(item_config):
            continue
        table_position = int(item_config.get("table_position") or 0)
        if table_position <= 0:
            continue

        source_name = str(item_name)
        result[source_name] = _SummaryItemConfig(
            source_name=source_name,
            display_name=str(item_config.get("display_name") or source_name),
            description=str(item_config.get("description") or ""),
            table_position=table_position,
            sources=tuple(str(source) for source in item_config.get("sources", item_config.get("platforms", []))),
            prefix=str(item_config.get("prefix") or ""),
            suffix=str(item_config.get("suffix") or ""),
            value_type=str(item_config.get("type") or ""),
            positive=bool(int(item_config.get("positive", 1))),
        )
    return result


def _format_metric_column_value(value: Any, metric: str, configs: dict[str, _SummaryItemConfig]) -> str:
    config = _summary_config(metric, configs)
    if config.value_type == "int":
        formatted_value = _format_int_value(value)
        if formatted_value == "":
            return ""
        return apply_number_affixes(formatted_value, prefix=config.prefix, suffix=config.suffix)
    return format_metric_value(value, prefix=config.prefix, suffix=config.suffix)


def _pvalue_background(
    pvalue: Any,
    diff_percent: Any,
    metric: str,
    metric_configs: dict[str, _SummaryItemConfig],
) -> str:
    pvalue_number = number_or_none(pvalue)
    diff_percent_number = number_or_none(diff_percent)
    if pvalue_number is None:
        return ""
    if pvalue_number >= 0.05 or diff_percent_number is None or diff_percent_number == 0:
        return PVALUE_NEUTRAL_COLOR

    metric_config = _summary_config(metric, metric_configs)
    is_good = diff_percent_number > 0 if metric_config.positive else diff_percent_number < 0
    return PVALUE_POSITIVE_COLOR if is_good else PVALUE_NEGATIVE_COLOR


def _format_int_value(value: Any) -> str:
    number_value = number_or_none(value)
    if number_value is None:
        return ""
    return str(int(round(number_value)))


def _summary_config(metric: Any, configs: dict[str, _SummaryItemConfig]) -> _SummaryItemConfig:
    return configs[str(metric)]


def _filter_configured_rows(df: pd.DataFrame, configs: dict[str, _SummaryItemConfig]) -> pd.DataFrame:
    if df.empty:
        return df

    rows = []
    for _, row in df.iterrows():
        config = configs.get(str(row["metric"]))
        if config is None:
            continue
        if config.sources and str(row["client"]) not in config.sources:
            continue
        rows.append(row)

    if not rows:
        return df.iloc[0:0].copy()

    result = pd.DataFrame(rows).reset_index(drop=True)
    result["table_position"] = result["metric"].map(lambda metric: configs[str(metric)].table_position)
    return result


def _sort_summary_rows(df: pd.DataFrame, extra_columns: list[str]) -> pd.DataFrame:
    if df.empty:
        return df
    return df.sort_values(["client", "segment", "table_position", *extra_columns]).reset_index(drop=True)


def _filtered_config_names(configs: dict[str, _SummaryItemConfig], names: Optional[Sequence[str]]) -> list[str]:
    config_names = list(configs)
    if not names:
        return config_names
    allowed_names = {str(name) for name in names}
    return [name for name in config_names if name in allowed_names]


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
