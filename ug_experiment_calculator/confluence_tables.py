from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
import uuid
from typing import Any, Optional

import pandas as pd

from .colors import (
    HEADER_COLOR,
    PVALUE_NEGATIVE_COLOR,
    PVALUE_NEUTRAL_COLOR,
    PVALUE_POSITIVE_COLOR,
)
from .config import ExperimentCalculatorConfig
from .confluence_charts import (
    build_metric_confluence_chart_code,
    build_metric_confluence_lift_chart_code,
    build_stat_confluence_chart_code,
)
from .metrics import config_enabled_for_domain, config_enabled_for_subdomain, load_metrics_config, normalize_metric_config
from .rollout import DEFAULT_IMPACT_LOOKBACK_DAYS
from .value_formatting import (
    apply_number_affixes,
    format_diff_percent,
    format_metric_number,
    format_pvalue,
    number_or_none,
)


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

STATS_TABLE_COLUMNS: tuple[str, ...] = (
    "dt",
    "metric",
    "variation",
    "value",
    "client",
    "segment",
)

DESIGN_TABLE_COLUMNS: tuple[str, ...] = (
    "Metrics",
    "Design / each metric",
    "Baseline",
    "Lift, %",
    "MDE",
    "Power",
    "Alpha",
    "Sample size (per variation)",
    "Duration (days)",
)

DESIGN_SUMMARY_ROW = "Design summary"
DESIGN_SAMPLE_ROW = "Sample"
DESIGN_DAYS_ROW = "Days"
DESIGN_SEPARATOR = "—"
ROLLOUT_IMPACT_EXPERIMENT_START_COLUMN = "Experiment Start"
ROLLOUT_IMPACT_TITLE = "Forecast (per day)"
ROLLOUT_IMPACT_MEMBERS_STAT = "members"
ROLLOUT_IMPACT_INSTALL_STAT = "install_cnt"
DEFAULT_ROLLOUT_IMPACT_STATS: tuple[str, ...] = ("subscriptions_cnt", "charge_cnt", "revenue")


@dataclass(frozen=True)
class _MetricTableConfig:
    name: str
    display_name: str
    table_position: int
    positive: bool
    prefix: str
    suffix: str
    value_type: str
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
    domain: str = "monetization",
    subdomain: str | None = None,
    config: Optional[ExperimentCalculatorConfig] = None,
    thousands_separator: bool = True,
) -> str:
    cfg = config or ExperimentCalculatorConfig.from_env()
    metric_configs = _load_metric_table_configs(metrics_yaml_path or cfg.metrics_yaml_path, domain=domain, subdomain=subdomain)
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
        domain=domain,
        subdomain=subdomain,
        thousands_separator=thousands_separator,
    )


def get_experiment_stats_confluence_table_data(
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
            `variation`,
            `value`,
            `client`,
            `segment`
        from {cfg.exp_stats_table}
        where
            {where_sql}
        order by
            `client`,
            `segment`,
            `dt`,
            `variation`,
            `metric`
    """
    return execute_sql(query)


def get_experiment_stats_confluence_table_code(
    exp_id: int,
    *,
    clients: Optional[Sequence[str]] = None,
    segments: Optional[Sequence[str]] = None,
    stats_yaml_path: str | Path | None = None,
    domain: str = "monetization",
    subdomain: str | None = None,
    config: Optional[ExperimentCalculatorConfig] = None,
    thousands_separator: bool = True,
) -> str:
    cfg = config or ExperimentCalculatorConfig.from_env()
    stats_configs = _load_metric_table_configs(stats_yaml_path or cfg.stats_yaml_path, domain=domain, subdomain=subdomain)
    rows = get_experiment_stats_confluence_table_data(
        exp_id,
        clients=clients,
        segments=segments,
        metrics=[stats_config.name for stats_config in stats_configs],
        config=cfg,
    )
    return build_experiment_stats_confluence_table_code(
        rows,
        stats_yaml_path=stats_yaml_path or cfg.stats_yaml_path,
        domain=domain,
        subdomain=subdomain,
        thousands_separator=thousands_separator,
    )


def get_rollout_impact_confluence_table_code(
    exp_id: int,
    *,
    clients: Optional[Sequence[str]] = None,
    segment_name: str = TOTAL_SEGMENT,
    stats: Optional[Sequence[str]] = None,
    stats_yaml_path: str | Path | None = None,
    metrics_yaml_path: str | Path | None = None,
    domain: str = "monetization",
    subdomain: str | None = None,
    lookback_days: int = DEFAULT_IMPACT_LOOKBACK_DAYS,
    date_end: Any = None,
    config: Optional[ExperimentCalculatorConfig] = None,
    thousands_separator: bool = True,
    update_split_users: bool = False,
    ensure_experiment_users: bool = False,
) -> str:
    from .rollout import calculate_rollout_impact_estimate

    cfg = config or ExperimentCalculatorConfig.from_env()
    significance_configs = _rollout_impact_significance_metric_configs(
        metrics_yaml_path or cfg.metrics_yaml_path,
        domain=domain,
        subdomain=subdomain,
    )
    metric_names = _rollout_impact_significance_metric_names(stats, significance_configs)
    stats_rows = get_experiment_stats_confluence_table_data(
        exp_id,
        clients=clients,
        segments=[segment_name],
        metrics=_rollout_impact_query_stats(stats),
        config=cfg,
    )
    metric_rows = (
        get_experiment_confluence_table_data(
            exp_id,
            clients=clients,
            segments=[segment_name],
            metrics=metric_names,
            config=cfg,
        )
        if metric_names
        else pd.DataFrame(columns=TABLE_COLUMNS)
    )
    impact_rows = calculate_rollout_impact_estimate(
        exp_id,
        clients=clients,
        segment_name=segment_name,
        lookback_days=lookback_days,
        date_end=date_end,
        update_split_users=update_split_users,
        ensure_experiment_users=ensure_experiment_users,
        config=cfg,
    )
    return build_rollout_impact_confluence_table_code(
        stats_rows,
        impact_rows,
        metric_rows=metric_rows,
        stats=stats,
        segment_name=segment_name,
        stats_yaml_path=stats_yaml_path or cfg.stats_yaml_path,
        metrics_yaml_path=metrics_yaml_path or cfg.metrics_yaml_path,
        domain=domain,
        subdomain=subdomain,
        thousands_separator=thousands_separator,
    )


def build_experiment_confluence_table_code(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    metrics_yaml_path: str | Path | None = None,
    domain: str = "monetization",
    subdomain: str | None = None,
    thousands_separator: bool = True,
) -> str:
    df = _prepare_table_rows(rows)
    cfg = ExperimentCalculatorConfig.from_env()
    metric_configs = _load_metric_table_configs(metrics_yaml_path or cfg.metrics_yaml_path, domain=domain, subdomain=subdomain)

    if df.empty or not metric_configs:
        return _rollout_impact_table("")

    client_names = _ordered_values(df["client"])
    client_blocks = []
    for client in client_names:
        client_df = df[df["client"] == client].copy()
        table_html = _build_client_table(
            client_df,
            metric_configs,
            thousands_separator=thousands_separator,
        )
        if not table_html:
            continue
        if len(client_names) > 1:
            client_blocks.append(_ui_expand(str(client), table_html))
        else:
            client_blocks.append(table_html)

    return "\n".join(client_blocks)


def build_experiment_stats_confluence_table_code(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    stats_yaml_path: str | Path | None = None,
    domain: str = "monetization",
    subdomain: str | None = None,
    thousands_separator: bool = True,
) -> str:
    df = _prepare_stats_table_rows(rows)
    cfg = ExperimentCalculatorConfig.from_env()
    stats_configs = _load_metric_table_configs(stats_yaml_path or cfg.stats_yaml_path, domain=domain, subdomain=subdomain)

    if df.empty or not stats_configs:
        return _ui_expand("Stats", _table([]))

    client_names = _ordered_values(df["client"])
    client_blocks = []
    for client in client_names:
        client_df = df[df["client"] == client].copy()
        table_html = _build_stats_client_table(
            client_df,
            stats_configs,
            thousands_separator=thousands_separator,
        )
        if not table_html:
            continue
        if len(client_names) > 1:
            client_blocks.append(_ui_expand(str(client), table_html))
        else:
            client_blocks.append(table_html)

    return _ui_expand("Stats", "\n".join(client_blocks) if client_blocks else _table([]))


def build_rollout_impact_confluence_table_code(
    stats_rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    impact_rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    *,
    metric_rows: pd.DataFrame | Iterable[Mapping[str, Any]] | None = None,
    stats: Optional[Sequence[str]] = None,
    segment_name: str = TOTAL_SEGMENT,
    stats_yaml_path: str | Path | None = None,
    metrics_yaml_path: str | Path | None = None,
    domain: str = "monetization",
    subdomain: str | None = None,
    thousands_separator: bool = True,
) -> str:
    df = _prepare_stats_table_rows(stats_rows)
    impact_df = _prepare_rollout_impact_rows(impact_rows)
    metric_df = _prepare_rollout_impact_metric_rows(metric_rows)
    cfg = ExperimentCalculatorConfig.from_env()
    stats_configs = _load_metric_table_configs(stats_yaml_path or cfg.stats_yaml_path, domain=domain, subdomain=subdomain)
    significance_configs = _rollout_impact_significance_metric_configs(
        metrics_yaml_path or cfg.metrics_yaml_path,
        domain=domain,
        subdomain=subdomain,
    )
    metric_configs = _rollout_impact_metric_configs(df, stats_configs, stats)

    if df.empty or impact_df.empty or not metric_configs:
        return _rollout_impact_table("")

    df = df[df["segment"] == str(segment_name)].copy()
    latest_df = _latest_stats_rows_by_client(df)
    metric_df = metric_df[metric_df["segment"] == str(segment_name)].copy()
    latest_metric_df = _latest_metric_rows_by_client(metric_df)
    variations = _rollout_impact_variations(latest_df)
    if latest_df.empty or not variations:
        return _rollout_impact_table("")

    clients = _rollout_impact_clients(latest_df, impact_df)
    if not clients:
        return _table([])

    row_specs = _rollout_impact_row_specs(variations)
    table_height = 2 + len(row_specs)
    blocks = [
        _prepare_rollout_impact_client_block(
            client,
            latest_df[latest_df["client"] == client].copy(),
            latest_metric_df[latest_metric_df["client"] == client].copy(),
            impact_df,
            metric_configs,
            significance_configs,
            row_specs,
            include_row_names=client_index == 0,
            thousands_separator=thousands_separator,
        )
        for client_index, client in enumerate(clients)
    ]

    rows = []
    for row_index in range(table_height):
        cells = []
        for block_index, block in enumerate(blocks):
            if block_index > 0 and row_index == 0:
                cells.append(_design_separator_cell(table_height))
            cells.extend(block[row_index])
        rows.append(_row(cells))

    return _rollout_impact_table(_table(rows))


def build_design_confluence_table_code(
    platform_frames: Mapping[str, pd.DataFrame],
    *,
    thousands_separator: bool = True,
) -> str:
    blocks = []
    for platform_index, (platform, df) in enumerate(platform_frames.items()):
        blocks.append(
            _prepare_design_platform_block(
                str(platform),
                df,
                include_row_names=platform_index == 0,
                thousands_separator=thousands_separator,
            )
        )
    if not blocks:
        return _table([])

    table_height = len(DESIGN_TABLE_COLUMNS) + 4
    rows = []
    for row_index in range(table_height):
        cells = []
        for block_index, block in enumerate(blocks):
            if block_index > 0 and row_index == 0:
                cells.append(_design_separator_cell(table_height))
            cells.extend(block[row_index])
        rows.append(_row(cells))

    return _table(rows)


def _build_client_table(
    df: pd.DataFrame,
    metric_configs: list[_MetricTableConfig],
    *,
    thousands_separator: bool,
) -> str:
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
        rows.extend(
            _build_segment_rows(
                segment_df,
                metric_configs,
                thousands_separator=thousands_separator,
            )
        )
    return _table(rows)


def _build_stats_client_table(
    df: pd.DataFrame,
    stats_configs: list[_MetricTableConfig],
    *,
    thousands_separator: bool,
) -> str:
    metric_names = set(df["metric"])
    stats_configs = [stats_config for stats_config in stats_configs if stats_config.name in metric_names]
    if not stats_configs:
        return ""

    rows = []
    segment_names = _ordered_segments(df["segment"])
    for segment_index, segment in enumerate(segment_names):
        segment_df = df[df["segment"] == segment].copy()
        if segment_index > 0 or segment != TOTAL_SEGMENT:
            rows.append(_segment_row(str(segment), len(stats_configs) + 1))
        rows.extend(
            _build_stats_segment_rows(
                segment_df,
                stats_configs,
                thousands_separator=thousands_separator,
            )
        )
    return _table(rows)


def _build_segment_rows(
    df: pd.DataFrame,
    metric_configs: list[_MetricTableConfig],
    *,
    thousands_separator: bool,
) -> list[str]:
    latest_df = _latest_rows(df)
    test_variations = _test_variations(latest_df)

    rows = [_header_row(metric_configs)]
    rows.append(_control_row(latest_df, metric_configs, thousands_separator=thousands_separator))

    for test_variation in test_variations:
        rows.append(
            _test_variation_row(
                latest_df,
                metric_configs,
                test_variation,
                thousands_separator=thousands_separator,
            )
        )
        rows.append(
            _lift_row(
                latest_df,
                metric_configs,
                test_variation,
                thousands_separator=thousands_separator,
            )
        )
        rows.append(
            _pvalue_row(
                latest_df,
                metric_configs,
                test_variation,
                thousands_separator=thousands_separator,
            )
        )

    rows.append(_cumulatives_row(df, metric_configs))
    return rows


def _build_stats_segment_rows(
    df: pd.DataFrame,
    stats_configs: list[_MetricTableConfig],
    *,
    thousands_separator: bool,
) -> list[str]:
    latest_df = _latest_stats_rows(df)
    variations = _stat_variations(latest_df)

    rows = [_header_row(stats_configs)]
    if any(str(variation) == "1" for variation in variations):
        rows.append(_stats_variation_row(latest_df, stats_configs, 1, "Control", thousands_separator=thousands_separator))

    for variation in variations:
        if str(variation) == "1":
            continue
        rows.append(
            _stats_variation_row(
                latest_df,
                stats_configs,
                variation,
                f"Variation {_format_variation(variation)}",
                thousands_separator=thousands_separator,
            )
        )

    rows.append(_stats_cumulatives_row(df, stats_configs))
    return rows


def _header_row(metric_configs: list[_MetricTableConfig]) -> str:
    cells = [_cell("Variation", background=HEADER_COLOR, bold=True, align="left")]
    cells.extend(
        _cell(metric_config.display_name, background=HEADER_COLOR, bold=True, align="left")
        for metric_config in metric_configs
    )
    return _row(cells)


def _stats_variation_row(
    df: pd.DataFrame,
    stats_configs: list[_MetricTableConfig],
    variation: Any,
    row_label: str,
    *,
    thousands_separator: bool,
) -> str:
    cells = [_row_header_cell(row_label)]
    for stats_config in stats_configs:
        row = _latest_stats_metric_row(df, stats_config.name, variation)
        cells.append(
            _cell(
                _format_stats_table_value(
                    _row_number(row, "value"),
                    stats_config,
                    thousands_separator=thousands_separator,
                )
            )
        )
    return _row(cells)


def _control_row(
    df: pd.DataFrame,
    metric_configs: list[_MetricTableConfig],
    *,
    thousands_separator: bool,
) -> str:
    cells = [_row_header_cell("Control")]
    for metric_config in metric_configs:
        metric_rows = df[df["metric"] == metric_config.name]
        cells.append(
            _cell(
                _format_metric_table_value(
                    _first_number(metric_rows, "mean_0"),
                    metric_config,
                    thousands_separator=thousands_separator,
                )
            )
        )
    return _row(cells)


def _test_variation_row(
    df: pd.DataFrame,
    metric_configs: list[_MetricTableConfig],
    test_variation: Any,
    *,
    thousands_separator: bool,
) -> str:
    cells = [_row_header_cell(f"Variation {_format_variation(test_variation)}")]
    for metric_config in metric_configs:
        row = _latest_metric_pair_row(df, metric_config.name, test_variation)
        cells.append(
            _cell(
                _format_metric_table_value(
                    _row_number(row, "mean_1"),
                    metric_config,
                    thousands_separator=thousands_separator,
                )
            )
        )
    return _row(cells)


def _lift_row(
    df: pd.DataFrame,
    metric_configs: list[_MetricTableConfig],
    test_variation: Any,
    *,
    thousands_separator: bool,
) -> str:
    cells = [_row_header_cell("diff, %")]
    for metric_config in metric_configs:
        row = _latest_metric_pair_row(df, metric_config.name, test_variation)
        cells.append(
            _cell(
                _format_diff_percent(
                    _row_number(row, "lift"),
                    thousands_separator=thousands_separator,
                )
            )
        )
    return _row(cells)


def _pvalue_row(
    df: pd.DataFrame,
    metric_configs: list[_MetricTableConfig],
    test_variation: Any,
    *,
    thousands_separator: bool,
) -> str:
    cells = [_row_header_cell("pvalue")]
    for metric_config in metric_configs:
        row = _latest_metric_pair_row(df, metric_config.name, test_variation)
        pvalue = _row_number(row, "pvalue")
        lift = _row_number(row, "lift")
        cells.append(
            _cell(
                _format_pvalue(pvalue, thousands_separator=thousands_separator),
                background=_pvalue_background(pvalue, lift, metric_config.positive),
            )
        )
    return _row(cells)


def _cumulatives_row(df: pd.DataFrame, metric_configs: list[_MetricTableConfig]) -> str:
    cells = [_row_header_cell("cumulatives")]
    for metric_config in metric_configs:
        metric_rows = df[df["metric"] == metric_config.name]
        if metric_rows.empty:
            cells.append(_cell(""))
            continue

        chart_code = "\n".join([
            build_metric_confluence_chart_code(metric_rows, metric_config.name),
            build_metric_confluence_lift_chart_code(metric_rows, metric_config.name),
        ])
        cells.append(_cell(chart_code, raw=True))
    return _row(cells)


def _stats_cumulatives_row(df: pd.DataFrame, stats_configs: list[_MetricTableConfig]) -> str:
    cells = [_row_header_cell("cumulatives")]
    for stats_config in stats_configs:
        metric_rows = df[df["metric"] == stats_config.name]
        if metric_rows.empty:
            cells.append(_cell(""))
            continue

        chart_code = build_stat_confluence_chart_code(metric_rows, stats_config.name)
        cells.append(_cell(chart_code, raw=True))
    return _row(cells)


def _prepare_rollout_impact_client_block(
    client: str,
    df: pd.DataFrame,
    metric_df: pd.DataFrame,
    impact_df: pd.DataFrame,
    metric_configs: list[_MetricTableConfig],
    significance_configs: Mapping[str, _MetricTableConfig],
    row_specs: list[tuple[str, Any]],
    *,
    include_row_names: bool,
    thousands_separator: bool,
) -> list[list[str]]:
    value_column_count = len(metric_configs) + 1
    expected_users = _rollout_impact_expected_users(impact_df, client)
    control_values = {
        metric_config.name: _rollout_impact_metric_estimate(df, metric_config.name, 1, expected_users)
        for metric_config in metric_configs
    }

    rows = []
    client_header_cells = [_cell("", background=HEADER_COLOR, bold=True, align="left")] if include_row_names else []
    client_header_cells.append(
        _cell(
            _format_rollout_client_name(client),
            background=HEADER_COLOR,
            bold=True,
            colspan=value_column_count,
            align="center",
        )
    )
    rows.append(client_header_cells)

    column_header_cells = [_row_header_cell("Variations")] if include_row_names else []
    column_header_cells.append(
        _cell(ROLLOUT_IMPACT_EXPERIMENT_START_COLUMN, background=HEADER_COLOR, bold=True, align="right")
    )
    column_header_cells.extend(
        _cell(metric_config.display_name, background=HEADER_COLOR, bold=True, align="right")
        for metric_config in metric_configs
    )
    rows.append(column_header_cells)

    for row_type, variation in row_specs:
        cells = [_row_header_cell(_rollout_impact_row_label(row_type, variation))] if include_row_names else []
        cells.append(_rollout_impact_experiment_start_cell(row_type, expected_users, thousands_separator=thousands_separator))
        for metric_config in metric_configs:
            value = _rollout_impact_row_value(
                df,
                metric_config.name,
                variation,
                row_type,
                expected_users,
                control_values.get(metric_config.name),
            )
            background = _rollout_impact_diff_background(
                metric_df,
                metric_config.name,
                variation,
                row_type,
                significance_configs,
            )
            cells.append(
                _cell(
                    _format_stats_table_value(value, metric_config, thousands_separator=thousands_separator),
                    background=background,
                )
            )
        rows.append(cells)

    return rows


def _rollout_impact_table(body: str) -> str:
    return "\n".join([
        f"<h2>{escape(ROLLOUT_IMPACT_TITLE)}</h2>",
        body or _table([]),
    ])


def _rollout_impact_metric_estimate(
    df: pd.DataFrame,
    metric: str,
    variation: Any,
    expected_users: float | None,
) -> float | None:
    if expected_users is None:
        return None

    members = _rollout_impact_stat_value(df, ROLLOUT_IMPACT_MEMBERS_STAT, variation)
    metric_value = _rollout_impact_stat_value(df, metric, variation)
    if members in (None, 0) or metric_value is None:
        return None

    return metric_value / members * expected_users


def _rollout_impact_row_value(
    df: pd.DataFrame,
    metric: str,
    variation: Any,
    row_type: str,
    expected_users: float | None,
    control_value: float | None,
) -> float | None:
    if row_type != "diff":
        return _rollout_impact_metric_estimate(df, metric, variation, expected_users)

    test_value = _rollout_impact_metric_estimate(df, metric, variation, expected_users)
    if test_value is None or control_value is None:
        return None
    return test_value - control_value


def _rollout_impact_diff_background(
    metric_df: pd.DataFrame,
    stat_name: str,
    variation: Any,
    row_type: str,
    significance_configs: Mapping[str, _MetricTableConfig],
) -> str | None:
    if row_type != "diff" or metric_df.empty:
        return None

    significance_config = significance_configs.get(stat_name)
    if significance_config is None:
        return None

    row = _latest_metric_pair_row(metric_df, significance_config.name, variation)
    pvalue = _row_number(row, "pvalue")
    lift = _row_number(row, "lift")
    return _pvalue_background(pvalue, lift, significance_config.positive)


def _rollout_impact_stat_value(df: pd.DataFrame, metric: str, variation: Any) -> float | None:
    row = _latest_stats_metric_row(df, metric, variation)
    return _row_number(row, "value")


def _rollout_impact_experiment_start_cell(
    row_type: str,
    expected_users: float | None,
    *,
    thousands_separator: bool,
) -> str:
    if row_type == "diff":
        return _cell("")

    value = _format_int_value(expected_users)
    if value and thousands_separator:
        value = _add_thousands_separator(value)
    return _cell(value)


def _rollout_impact_metric_configs(
    df: pd.DataFrame,
    stats_configs: list[_MetricTableConfig],
    stats: Optional[Sequence[str]],
) -> list[_MetricTableConfig]:
    configs_by_name = {stats_config.name: stats_config for stats_config in stats_configs}
    if stats is None:
        stat_names = list(DEFAULT_ROLLOUT_IMPACT_STATS)
        if ROLLOUT_IMPACT_INSTALL_STAT in set(df["metric"].dropna().astype(str)):
            stat_names.append(ROLLOUT_IMPACT_INSTALL_STAT)
    else:
        stat_names = [str(stat) for stat in stats]

    requested = [configs_by_name[name] for name in stat_names if name in configs_by_name]
    return sorted(requested, key=lambda item: (item.table_position, item.source_index, item.name))


def _rollout_impact_query_stats(stats: Optional[Sequence[str]]) -> list[str]:
    stat_names = list(stats) if stats is not None else [
        *DEFAULT_ROLLOUT_IMPACT_STATS,
        ROLLOUT_IMPACT_INSTALL_STAT,
    ]
    result = [ROLLOUT_IMPACT_MEMBERS_STAT]
    for stat in stat_names:
        stat_name = str(stat)
        if stat_name not in result:
            result.append(stat_name)
    return result


def _rollout_impact_significance_metric_names(
    stats: Optional[Sequence[str]],
    significance_configs: Mapping[str, _MetricTableConfig],
) -> list[str]:
    stat_names = _rollout_impact_query_stats(stats)
    result = []
    for stat_name in stat_names:
        significance_config = significance_configs.get(stat_name)
        if significance_config is None or significance_config.name in result:
            continue
        result.append(significance_config.name)
    return result


def _rollout_impact_significance_metric_configs(
    metrics_yaml_path: str | Path,
    *,
    domain: str | None = None,
    subdomain: str | None = None,
) -> dict[str, _MetricTableConfig]:
    metrics_config = load_metrics_config(metrics_yaml_path)
    result: dict[str, _MetricTableConfig] = {}
    for metric_index, (metric_name, metric_items) in enumerate(metrics_config.items()):
        metric_config = normalize_metric_config(metric_items)
        if not config_enabled_for_domain(metric_config, domain):
            continue
        if not config_enabled_for_subdomain(metric_config, subdomain):
            continue
        numerator = str(metric_config.get("numerator") or "")
        table_position = int(metric_config.get("table_position") or 0)
        if not numerator or table_position <= 0:
            continue

        item = _MetricTableConfig(
            name=metric_name,
            display_name=str(metric_config.get("display_name") or metric_name),
            table_position=table_position,
            positive=bool(int(metric_config.get("positive", 1))),
            prefix=str(metric_config.get("prefix") or ""),
            suffix=str(metric_config.get("suffix") or ""),
            value_type=str(metric_config.get("type") or ""),
            source_index=metric_index,
        )
        if numerator not in result or _metric_config_sort_key(item) < _metric_config_sort_key(result[numerator]):
            result[numerator] = item

    return result


def _metric_config_sort_key(metric_config: _MetricTableConfig) -> tuple[int, int, str]:
    return (metric_config.table_position, metric_config.source_index, metric_config.name)


def _prepare_rollout_impact_rows(rows: pd.DataFrame | Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    if df.empty:
        return pd.DataFrame(columns=["client", "expected_affected_users"])

    missing_columns = {"client"}.difference(df.columns)
    if missing_columns:
        missing_columns_str = ", ".join(sorted(missing_columns))
        raise ValueError(f"Missing rollout impact columns: {missing_columns_str}")

    if "expected_affected_users" not in df.columns:
        if {"average_daily_users", "experiment_share"}.issubset(df.columns):
            df["expected_affected_users"] = (
                pd.to_numeric(df["average_daily_users"], errors="coerce")
                * pd.to_numeric(df["experiment_share"], errors="coerce")
            )
        else:
            raise ValueError("Missing rollout impact column: expected_affected_users")

    df["client"] = df["client"].astype(str)
    df["expected_affected_users"] = pd.to_numeric(df["expected_affected_users"], errors="coerce")
    return df[["client", "expected_affected_users"]].reset_index(drop=True)


def _prepare_rollout_impact_metric_rows(rows: pd.DataFrame | Iterable[Mapping[str, Any]] | None) -> pd.DataFrame:
    if rows is None:
        return pd.DataFrame(columns=[*TABLE_COLUMNS, "test_variation"])
    return _prepare_table_rows(rows)


def _latest_stats_rows_by_client(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values("dt")
        .groupby(["client", "segment", "metric", "variation"], as_index=False, dropna=False)
        .tail(1)
        .reset_index(drop=True)
    )


def _latest_metric_rows_by_client(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return (
        df.sort_values("dt")
        .groupby(["client", "segment", "metric", "variation_pair"], as_index=False, dropna=False)
        .tail(1)
        .reset_index(drop=True)
    )


def _rollout_impact_variations(df: pd.DataFrame) -> list[Any]:
    variations = _stat_variations(df)
    control_values = [variation for variation in variations if str(variation) == "1"]
    test_values = [variation for variation in variations if str(variation) != "1"]
    return control_values + test_values


def _rollout_impact_row_specs(variations: Sequence[Any]) -> list[tuple[str, Any]]:
    rows = []
    if any(str(variation) == "1" for variation in variations):
        rows.append(("control", 1))

    for variation in variations:
        if str(variation) == "1":
            continue
        rows.append(("variation", variation))
        rows.append(("diff", variation))
    return rows


def _rollout_impact_clients(stats_df: pd.DataFrame, impact_df: pd.DataFrame) -> list[str]:
    result = []
    for client in _ordered_values(impact_df["client"]):
        client_text = str(client)
        if client_text not in result:
            result.append(client_text)
    for client in _ordered_values(stats_df["client"]):
        client_text = str(client)
        if client_text not in result:
            result.append(client_text)
    return sorted(result, key=_rollout_impact_client_sort_key)


def _rollout_impact_client_sort_key(client: str) -> tuple[int, int, str]:
    client_text = str(client)
    ordered_clients = {
        "UG_WEB": (0, 0),
        "UGT_IOS": (1, 0),
        "UG_IOS": (1, 1),
        "UGT_ANDROID": (2, 0),
        "UG_ANDROID": (2, 1),
    }
    group, priority = ordered_clients.get(client_text, (3, 0))
    return (group, priority, client_text)


def _rollout_impact_expected_users(impact_df: pd.DataFrame, client: str) -> float | None:
    rows = impact_df[impact_df["client"] == str(client)]
    if rows.empty:
        return None
    return _number_or_none(rows.iloc[0]["expected_affected_users"])


def _rollout_impact_row_label(row_type: str, variation: Any) -> str:
    if row_type == "control":
        return "control"
    if row_type == "diff":
        return "diff"
    return f"variation {_format_variation(variation)}"


def _format_rollout_client_name(client: Any) -> str:
    return _format_text(client)


def _prepare_design_platform_block(
    platform: str,
    df: pd.DataFrame,
    *,
    include_row_names: bool,
    thousands_separator: bool,
) -> list[list[str]]:
    prepared_df = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame(df)
    source_columns = _design_source_columns(prepared_df)
    values_by_row = _design_values_by_row(prepared_df, source_columns, thousands_separator=thousands_separator)
    value_count = max(len(prepared_df.index), 1)

    rows = []
    rows.append([
        _cell(
            platform,
            background=HEADER_COLOR,
            bold=True,
            colspan=value_count + int(include_row_names),
            align="center",
        )
    ])

    for row_index, row_name in enumerate(DESIGN_TABLE_COLUMNS):
        cells = [_row_header_cell(row_name)] if include_row_names else []
        cells.extend(
            _design_value_cell(
                value,
                row_name,
                background=HEADER_COLOR if row_index == 0 else None,
                bold=row_index == 0,
                italic=row_index != 0,
                align="left" if row_index == 0 else "right",
            )
            for value in _pad_design_values(values_by_row[row_name], value_count)
        )
        rows.append(cells)

    rows.append([
        _cell(
            DESIGN_SUMMARY_ROW,
            background=HEADER_COLOR,
            bold=True,
            colspan=value_count + int(include_row_names),
            align="left",
        )
    ])
    rows.append(_design_summary_value_row(
        DESIGN_SAMPLE_ROW,
        values_by_row["Sample size (per variation)"],
        value_count,
        include_row_name=include_row_names,
    ))
    rows.append(_design_summary_value_row(
        DESIGN_DAYS_ROW,
        values_by_row["Duration (days)"],
        value_count,
        include_row_name=include_row_names,
    ))
    return rows


def _design_source_columns(df: pd.DataFrame) -> list[Any]:
    if all(column in df.columns for column in DESIGN_TABLE_COLUMNS):
        return list(DESIGN_TABLE_COLUMNS)
    return list(df.columns[:len(DESIGN_TABLE_COLUMNS)])


def _design_values_by_row(
    df: pd.DataFrame,
    source_columns: list[Any],
    *,
    thousands_separator: bool,
) -> dict[str, list[str]]:
    result = {}
    metric_values = _design_column_values(df, source_columns, "Metrics")
    for column_index, row_name in enumerate(DESIGN_TABLE_COLUMNS):
        if column_index >= len(source_columns):
            result[row_name] = [""] * len(df.index)
            continue

        source_column = source_columns[column_index]
        result[row_name] = [
            _format_design_table_value(
                value,
                row_name,
                metric=metric_values[row_index] if row_index < len(metric_values) else "",
                thousands_separator=thousands_separator,
            )
            for row_index, value in enumerate(df[source_column].tolist())
        ]
    return result


def _design_column_values(df: pd.DataFrame, source_columns: list[Any], row_name: str) -> list[Any]:
    column_index = DESIGN_TABLE_COLUMNS.index(row_name)
    if column_index >= len(source_columns):
        return []
    return df[source_columns[column_index]].tolist()


def _format_design_table_value(
    value: Any,
    row_name: str,
    *,
    metric: Any,
    thousands_separator: bool,
) -> str:
    if row_name in {"Metrics", "Design / each metric"}:
        return _format_text(value)

    if row_name == "Baseline":
        return _format_design_baseline(value, metric=metric, thousands_separator=thousands_separator)
    if row_name == "Lift, %":
        formatted_value = _format_diff_percent(value, thousands_separator=thousands_separator)
        return formatted_value or _format_text(value)
    if row_name == "Alpha":
        formatted_value = _format_pvalue(value, thousands_separator=thousands_separator)
        return formatted_value or _format_text(value)
    if row_name in {"Sample size (per variation)", "Duration (days)"}:
        return _format_design_integer(value, thousands_separator=thousands_separator)

    formatted_value = format_metric_number(value)
    if formatted_value == "":
        return _format_text(value)
    if thousands_separator:
        formatted_value = _add_thousands_separator(formatted_value)
    return formatted_value


def _format_design_baseline(value: Any, *, metric: Any, thousands_separator: bool) -> str:
    formatted_value = format_metric_number(value)
    if formatted_value == "":
        return _format_text(value)
    if thousands_separator:
        formatted_value = _add_thousands_separator(formatted_value)

    prefix, suffix = _design_baseline_affixes(metric)
    return apply_number_affixes(formatted_value, prefix=prefix, suffix=suffix)


def _design_baseline_affixes(metric: Any) -> tuple[str, str]:
    metric_text = _format_text(metric)
    if ", %" in metric_text:
        return "", "%"
    if ", $" in metric_text:
        return "$", ""
    return "", ""


def _design_value_cell(
    value: str,
    row_name: str,
    *,
    background: str | None,
    bold: bool,
    italic: bool,
    align: str,
) -> str:
    if row_name == "Design / each metric":
        return _cell(
            _format_raw_design_link(value),
            background=background,
            bold=bold,
            italic=italic,
            raw=True,
            align=align,
        )

    return _cell(value, background=background, bold=bold, italic=italic, align=align)


def _format_raw_design_link(value: str) -> str:
    value = str(value)
    if value.startswith("<p>") or value.startswith("<ac:"):
        return value
    return f"<p>{value}</p>"


def _format_design_integer(value: Any, *, thousands_separator: bool) -> str:
    number_value = number_or_none(value)
    if number_value is None:
        return _format_text(value)

    formatted_value = str(int(round(number_value)))
    if thousands_separator:
        formatted_value = _add_thousands_separator(formatted_value)
    return formatted_value


def _pad_design_values(values: list[str], value_count: int) -> list[str]:
    return values + [""] * max(0, value_count - len(values))


def _design_summary_value_row(
    row_name: str,
    values: list[str],
    value_count: int,
    *,
    include_row_name: bool,
) -> list[str]:
    max_index = _max_design_value_index(values)
    cells = [_row_header_cell(row_name)] if include_row_name else []
    for value_index in range(value_count):
        if value_index == max_index:
            cells.append(_cell(values[value_index], bold=True, italic=True))
        else:
            cells.append(_cell(DESIGN_SEPARATOR, italic=True))
    return cells


def _max_design_value_index(values: list[str]) -> int | None:
    max_index = None
    max_value = None
    for value_index, value in enumerate(values):
        number_value = number_or_none(str(value).replace(",", ""))
        if number_value is None:
            continue
        if max_value is None or number_value > max_value:
            max_index = value_index
            max_value = number_value
    return max_index


def _design_separator_cell(rowspan: int) -> str:
    return _cell(
        "",
        background=HEADER_COLOR,
        rowspan=rowspan,
        raw=True,
    )


def _load_metric_table_configs(
    metrics_yaml_path: str | Path,
    *,
    domain: str | None = None,
    subdomain: str | None = None,
) -> list[_MetricTableConfig]:
    metrics_config = load_metrics_config(metrics_yaml_path)
    result = []
    for metric_index, (metric_name, metric_items) in enumerate(metrics_config.items()):
        metric_config = normalize_metric_config(metric_items)
        if not config_enabled_for_domain(metric_config, domain):
            continue
        if not config_enabled_for_subdomain(metric_config, subdomain):
            continue
        table_position = int(metric_config.get("table_position") or 0)
        if table_position <= 0:
            continue

        result.append(_MetricTableConfig(
            name=metric_name,
            display_name=str(metric_config.get("display_name") or metric_name),
            table_position=table_position,
            positive=bool(int(metric_config.get("positive", 1))),
            prefix=str(metric_config.get("prefix") or ""),
            suffix=str(metric_config.get("suffix") or ""),
            value_type=str(metric_config.get("type") or ""),
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


def _prepare_stats_table_rows(rows: pd.DataFrame | Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    if df.empty:
        for column in STATS_TABLE_COLUMNS:
            if column not in df.columns:
                df[column] = pd.Series(dtype="object")
        return df

    missing_columns = set(STATS_TABLE_COLUMNS).difference(df.columns)
    if missing_columns:
        missing_columns_str = ", ".join(sorted(missing_columns))
        raise ValueError(f"Missing Confluence stats table columns: {missing_columns_str}")

    df["dt"] = pd.to_datetime(df["dt"], errors="coerce")
    df = df.dropna(subset=["dt", "metric", "variation", "client", "segment"]).copy()

    for column in ("metric", "client", "segment"):
        df[column] = df[column].astype(str)
    df["variation"] = df["variation"].map(_normalize_variation_value)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    sort_columns = ["client", "segment", "metric", "variation", "dt"]
    return df.sort_values(sort_columns, key=_stats_sort_key).reset_index(drop=True)


def _latest_rows(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values("dt")
        .groupby(["metric", "variation_pair"], as_index=False, dropna=False)
        .tail(1)
        .reset_index(drop=True)
    )


def _latest_stats_rows(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values("dt")
        .groupby(["metric", "variation"], as_index=False, dropna=False)
        .tail(1)
        .reset_index(drop=True)
    )


def _latest_metric_pair_row(df: pd.DataFrame, metric: str, test_variation: Any) -> pd.Series | None:
    row = df[(df["metric"] == metric) & (df["test_variation"].map(str) == str(test_variation))]
    if row.empty:
        return None
    return row.iloc[0]


def _latest_stats_metric_row(df: pd.DataFrame, metric: str, variation: Any) -> pd.Series | None:
    row = df[(df["metric"] == metric) & (df["variation"].map(str) == str(variation))]
    if row.empty:
        return None
    return row.iloc[0]


def _test_variations(df: pd.DataFrame) -> list[Any]:
    values = df["test_variation"].dropna().drop_duplicates().tolist()
    return sorted(values, key=_variation_sort_key)


def _stat_variations(df: pd.DataFrame) -> list[Any]:
    values = df["variation"].dropna().drop_duplicates().tolist()
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
    return number_or_none(value)


def _format_metric_table_value(
    value: Any,
    metric_config: _MetricTableConfig,
    *,
    thousands_separator: bool,
) -> str:
    formatted_value = format_metric_number(value)
    if formatted_value == "":
        return ""
    if thousands_separator:
        formatted_value = _add_thousands_separator(formatted_value)
    return apply_number_affixes(formatted_value, prefix=metric_config.prefix, suffix=metric_config.suffix)


def _format_stats_table_value(
    value: Any,
    stats_config: _MetricTableConfig,
    *,
    thousands_separator: bool,
) -> str:
    if stats_config.value_type == "int":
        formatted_value = _format_int_value(value)
        if formatted_value == "":
            return ""
        if thousands_separator:
            formatted_value = _add_thousands_separator(formatted_value)
        return apply_number_affixes(formatted_value, prefix=stats_config.prefix, suffix=stats_config.suffix)

    return _format_metric_table_value(value, stats_config, thousands_separator=thousands_separator)


def _format_int_value(value: Any) -> str:
    number_value = _number_or_none(value)
    if number_value is None:
        return ""
    return str(int(round(number_value)))


def _format_diff_percent(value: Any, *, thousands_separator: bool) -> str:
    formatted_value = format_diff_percent(value)
    if formatted_value == "" or not thousands_separator:
        return formatted_value
    number_part, percent_sign = formatted_value.removesuffix("%"), "%"
    return f"{_add_thousands_separator(number_part)}{percent_sign}"


def _format_pvalue(value: Any, *, thousands_separator: bool) -> str:
    formatted_value = format_pvalue(value)
    if formatted_value == "" or not thousands_separator:
        return formatted_value
    return _add_thousands_separator(formatted_value)


def _add_thousands_separator(value: str) -> str:
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


def _format_variation(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _format_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
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


def _stats_sort_key(series: pd.Series) -> pd.Series:
    if series.name == "variation":
        return pd.to_numeric(series, errors="coerce").fillna(float("inf"))
    return series


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
    return _cell(value, background=HEADER_COLOR, bold=True, align="left")


def _segment_row(segment: str, colspan: int) -> str:
    return _row([_cell(segment, background=HEADER_COLOR, bold=True, colspan=colspan, align="left")])


def _cell(
    value: str,
    *,
    background: str | None = None,
    bold: bool = False,
    italic: bool = False,
    colspan: int | None = None,
    rowspan: int | None = None,
    raw: bool = False,
    align: str = "right",
) -> str:
    attributes = _cell_attributes(
        background=background,
        colspan=colspan,
        rowspan=rowspan,
        align=align,
        italic=raw and italic,
    )

    if raw:
        return f"<td{attributes}>{value}</td>"

    escaped_value = escape(str(value))
    if italic:
        escaped_value = f"<em>{escaped_value}</em>"
    if bold:
        escaped_value = f"<strong>{escaped_value}</strong>"
    return f"<td{attributes}><p>{escaped_value}</p></td>"


def _cell_attributes(
    *,
    background: str | None,
    colspan: int | None,
    rowspan: int | None,
    align: str,
    italic: bool,
) -> str:
    attributes = []
    if background:
        escaped_background = escape(background)
        attributes.append(f'class="highlight-{escaped_background} confluenceTd"')
        attributes.append(f'data-highlight-colour="{escaped_background}"')
        attributes.append(f'bgcolor="{escaped_background}"')
    styles = []
    if align:
        styles.append(f"text-align:{escape(align)}")
    if italic:
        styles.append("font-style:italic")
    if styles:
        attributes.append(f'style="{";".join(styles)}"')
    if colspan is not None:
        attributes.append(f'colspan="{int(colspan)}"')
    if rowspan is not None:
        attributes.append(f'rowspan="{int(rowspan)}"')

    return " " + " ".join(attributes) if attributes else ""


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
