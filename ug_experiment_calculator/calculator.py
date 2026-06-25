from __future__ import annotations

import logging
from typing import Optional

from clickhouse_worker import execute_sql
import pandas as pd

from .config import ExperimentCalculatorConfig
from .metrics import (
    calc_cumulative_aggregates,
    calc_cumulative_funnel_aggregates,
    calc_funnel_stats_by_variation_pairs,
    calc_metrics_stats_by_variation_pairs,
    funnel_enabled_for_client,
    load_funnels_config,
    normalize_funnel_config,
    stats_columns_for_client,
)
from .repository import (
    create_exp_funnel_results_table,
    create_exp_funnel_stats_table,
    create_exp_results_table,
    create_exp_stats_table,
    create_experiment_users_table,
    create_experiment_users_slice_segments,
    create_experiments_subscription_table,
    drop_exp_partitions,
    drop_table,
    ensure_table_columns,
    get_experiment,
    get_experiment_users_hash,
    get_funnel_metrics,
    get_monetization_metrics,
    get_retention_metrics,
    get_tab_view_metrics,
    get_user_filters_hash,
    is_mobweb_segment,
    update_exp_results_table,
    update_subscription_source_tables,
)
from .rollout import update_rollout_split_users_daily


logger = logging.getLogger(__name__)


FUNNEL_DEFINITION_COLUMNS = {
    "funnel_definition_key": "String",
    "funnel_definition_name": "String",
    "funnel_definition_description": "String",
}


RETENTION_COUNT_COLUMNS = [
    "web_retention_1d_cnt",
    "web_retention_7d_cnt",
    "web_retention_14d_cnt",
    "app_retention_1d_cnt",
    "app_retention_7d_cnt",
    "app_retention_14d_cnt",
    "mobweb_app_retention_1d_cnt",
    "mobweb_app_retention_7d_cnt",
    "mobweb_app_retention_14d_cnt",
]


TAB_VIEW_METRIC_COLUMNS = [
    "web_tab_view_60s_user_cnt",
    "web_tab_view_120s_user_cnt",
    "web_tab_view_180s_user_cnt",
    "web_tab_view_300s_user_cnt",
    "web_tab_view_600s_user_cnt",
    "web_tab_view_events_cnt",
    "web_tab_view_60s_events_cnt",
    "web_tab_view_120s_events_cnt",
    "web_tab_view_180s_events_cnt",
    "web_tab_view_300s_events_cnt",
    "web_tab_view_600s_events_cnt",
    "web_tab_view_events_per_user_var",
    "web_tab_view_60s_events_per_user_var",
    "web_tab_view_120s_events_per_user_var",
    "web_tab_view_180s_events_per_user_var",
    "web_tab_view_300s_events_per_user_var",
    "web_tab_view_600s_events_per_user_var",
    "app_tab_view_60s_user_cnt",
    "app_tab_view_120s_user_cnt",
    "app_tab_view_180s_user_cnt",
    "app_tab_view_300s_user_cnt",
    "app_tab_view_600s_user_cnt",
    "app_tab_view_events_cnt",
    "app_tab_view_60s_events_cnt",
    "app_tab_view_120s_events_cnt",
    "app_tab_view_180s_events_cnt",
    "app_tab_view_300s_events_cnt",
    "app_tab_view_600s_events_cnt",
    "app_tab_view_events_per_user_var",
    "app_tab_view_60s_events_per_user_var",
    "app_tab_view_120s_events_per_user_var",
    "app_tab_view_180s_events_per_user_var",
    "app_tab_view_300s_events_per_user_var",
    "app_tab_view_600s_events_per_user_var",
    "mobweb_app_tab_view_60s_user_cnt",
    "mobweb_app_tab_view_120s_user_cnt",
    "mobweb_app_tab_view_180s_user_cnt",
    "mobweb_app_tab_view_300s_user_cnt",
    "mobweb_app_tab_view_600s_user_cnt",
    "mobweb_app_tab_view_events_cnt",
    "mobweb_app_tab_view_60s_events_cnt",
    "mobweb_app_tab_view_120s_events_cnt",
    "mobweb_app_tab_view_180s_events_cnt",
    "mobweb_app_tab_view_300s_events_cnt",
    "mobweb_app_tab_view_600s_events_cnt",
    "mobweb_app_tab_view_events_per_user_var",
    "mobweb_app_tab_view_60s_events_per_user_var",
    "mobweb_app_tab_view_120s_events_per_user_var",
    "mobweb_app_tab_view_180s_events_per_user_var",
    "mobweb_app_tab_view_300s_events_per_user_var",
    "mobweb_app_tab_view_600s_events_per_user_var",
]


def _merge_metric_frame(df: pd.DataFrame, metric_df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if metric_df.empty:
        result = df.copy()
        for column in columns:
            result[column] = 0
        return result

    result = df.merge(metric_df, on=["dt", "variation"], how="left")
    for column in columns:
        if column not in result.columns:
            result[column] = 0
        result[column] = result[column].fillna(0)
    return result


def _merge_retention_metrics(df: pd.DataFrame, retention_df: pd.DataFrame) -> pd.DataFrame:
    return _merge_metric_frame(df, retention_df, RETENTION_COUNT_COLUMNS)


def _merge_tab_view_metrics(df: pd.DataFrame, tab_view_df: pd.DataFrame) -> pd.DataFrame:
    return _merge_metric_frame(df, tab_view_df, TAB_VIEW_METRIC_COLUMNS)


def _replace_exp_output_table(
    df: pd.DataFrame,
    *,
    exp_id: int,
    client: str,
    segment_name: str,
    logical_table_name: str,
    full_table_name: str,
    create_table_func,
    config: ExperimentCalculatorConfig,
    required_columns: Optional[dict[str, str]] = None,
) -> None:
    is_exists = execute_sql(f"exists {full_table_name}")
    if int(is_exists.iloc[0].values[0]) == 0:
        if df.empty:
            logger.info("Skipping %s creation: dataframe is empty", full_table_name)
            return
        create_table_func(df, config=config)
        return

    if required_columns:
        ensure_table_columns(logical_table_name, required_columns, config=config)

    drop_exp_partitions(exp_id, client_name=client, segment=segment_name, table_name=logical_table_name, config=config)
    if df.empty:
        logger.info("Skipping %s insert: dataframe is empty", full_table_name)
        return

    update_exp_results_table(df, table=logical_table_name, config=config)


def _calculate_exp_segment_info(
    *,
    exp_info: dict,
    funnels_config: dict,
    exp_users_table: str,
    client: str,
    segment_name: str,
    segment: dict,
    segment_hash: str,
    df_tot: dict,
    df_cum_agg_tot: dict,
    stats_df_tot: dict,
    retention_cache: dict,
    tab_view_cache: dict,
    product_metrics_segments: dict,
    config: ExperimentCalculatorConfig,
) -> str:
    exp_id = exp_info["id"]
    cfg = config

    logger.info("Loading subscriptions")
    subscription_table = create_experiments_subscription_table(exp_info, client, segment, config=cfg)
    logger.info("exp_users_table=%s, subscription_table=%s", exp_users_table, subscription_table)

    logger.info("Loading monetization metrics")
    df = get_monetization_metrics(
        exp_info,
        exp_users_table,
        subscription_table,
        client,
        segment_name,
        segment_hash,
        config=cfg,
    )
    retention_cache_key = (
        client,
        get_user_filters_hash(segment, client=client, clients_options=exp_info.get("clients_options", "")),
    )
    product_metrics_source_segment = product_metrics_segments.get(retention_cache_key)
    include_product_metrics = product_metrics_source_segment is None
    if include_product_metrics:
        product_metrics_segments[retention_cache_key] = segment_name

    if include_product_metrics and retention_cache_key not in retention_cache:
        logger.info("Loading retention metrics")
        retention_cache[retention_cache_key] = get_retention_metrics(
            exp_users_table,
            client,
            segment_name,
            segment_hash,
            calculate_app_retention=(
                client != "UG_WEB"
                or is_mobweb_segment(segment, exp_info.get("clients_options", ""), client)
            ),
            config=cfg,
        )
    elif include_product_metrics:
        logger.info(
            "Reusing retention metrics for exp_id=%s, client=%s, segment=%s",
            exp_id,
            client,
            segment_name,
        )
    else:
        logger.info(
            "Skipping retention metrics for exp_id=%s, client=%s, segment=%s: product metrics already attached to segment=%s for the same user filters",
            exp_id,
            client,
            segment_name,
            product_metrics_source_segment,
        )

    if include_product_metrics:
        df = _merge_retention_metrics(df, retention_cache[retention_cache_key])

    if include_product_metrics and retention_cache_key not in tab_view_cache:
        logger.info("Loading tab view metrics")
        tab_view_cache[retention_cache_key] = get_tab_view_metrics(
            exp_info,
            exp_users_table,
            client,
            segment_name,
            segment_hash,
            calculate_app_tab_view=(
                client != "UG_WEB"
                or is_mobweb_segment(segment, exp_info.get("clients_options", ""), client)
            ),
            config=cfg,
        )
    elif include_product_metrics:
        logger.info(
            "Reusing tab view metrics for exp_id=%s, client=%s, segment=%s",
            exp_id,
            client,
            segment_name,
        )
    else:
        logger.info(
            "Skipping tab view metrics for exp_id=%s, client=%s, segment=%s: product metrics already attached to segment=%s for the same user filters",
            exp_id,
            client,
            segment_name,
            product_metrics_source_segment,
        )

    if include_product_metrics:
        df = _merge_tab_view_metrics(df, tab_view_cache[retention_cache_key])
    df_tot[(client, segment_name)] = df

    logger.info("Loading subscription funnels")
    funnel_parts = []
    for funnel_definition_key, funnel_items in funnels_config.items():
        funnel_config = normalize_funnel_config(funnel_items)
        if not funnel_enabled_for_client(funnel_config, client):
            continue

        query_name = funnel_config.get("query", funnel_definition_key)
        current_funnel_df = get_funnel_metrics(
            query_name,
            exp_users_table,
            subscription_table,
            client,
            segment_name,
            segment_hash,
            config=cfg,
        )
        if current_funnel_df.empty:
            continue

        current_funnel_df["funnel_definition_key"] = funnel_definition_key
        current_funnel_df["funnel_definition_name"] = funnel_config.get("name", funnel_definition_key)
        current_funnel_df["funnel_definition_description"] = funnel_config.get("description", "")
        funnel_parts.append(current_funnel_df)

    funnel_df = pd.concat(funnel_parts, ignore_index=True) if funnel_parts else pd.DataFrame()

    logger.info("Calculating cumulative funnel aggregates")
    funnel_cum_df = calc_cumulative_funnel_aggregates(funnel_df)

    logger.info("Calculating cumulative funnel statistics")
    funnel_stats_df = calc_funnel_stats_by_variation_pairs(
        cumulative_df=funnel_cum_df,
        control_variation=1,
    )

    funnel_cum_df["exp_id"] = exp_id
    funnel_cum_df["client"] = client
    funnel_cum_df["segment"] = segment_name
    funnel_stats_df["exp_id"] = exp_id
    funnel_stats_df["client"] = client
    funnel_stats_df["segment"] = segment_name

    _replace_exp_output_table(
        funnel_cum_df,
        exp_id=exp_id,
        client=client,
        segment_name=segment_name,
        logical_table_name="ug_exp_funnel_stats",
        full_table_name=cfg.exp_funnel_stats_table,
        create_table_func=create_exp_funnel_stats_table,
        required_columns=FUNNEL_DEFINITION_COLUMNS,
        config=cfg,
    )
    _replace_exp_output_table(
        funnel_stats_df,
        exp_id=exp_id,
        client=client,
        segment_name=segment_name,
        logical_table_name="ug_exp_funnel_results",
        full_table_name=cfg.exp_funnel_results_table,
        create_table_func=create_exp_funnel_results_table,
        required_columns=FUNNEL_DEFINITION_COLUMNS,
        config=cfg,
    )

    logger.info("Deleting temporary subscription table")
    drop_table(subscription_table, config=cfg)

    logger.info("Calculating cumulative aggregates")
    df_cum_agg = calc_cumulative_aggregates(df)

    logger.info("Calculating cumulative statistics")
    stats_df = calc_metrics_stats_by_variation_pairs(
        cumulative_df=df_cum_agg,
        metrics_yaml_path=cfg.metrics_yaml_path,
        control_variation=1,
        client=client,
        segment=segment,
        clients_options=exp_info.get("clients_options", ""),
        domain=None if include_product_metrics else "monetization",
    )

    stats_metric_columns = stats_columns_for_client(
        cfg.stats_yaml_path,
        client,
        segment=segment,
        clients_options=exp_info.get("clients_options", ""),
        domain=None if include_product_metrics else "monetization",
    )
    stats_metric_columns = [col for col in df_cum_agg.columns if col in stats_metric_columns]
    df_cum_agg = df_cum_agg[["dt", "variation", *stats_metric_columns]]
    df_cum_agg = df_cum_agg.melt(id_vars=["dt", "variation"], var_name="metric", value_name="value")
    df_cum_agg["exp_id"] = exp_id
    df_cum_agg["client"] = client
    df_cum_agg["segment"] = segment_name
    df_cum_agg_tot[(client, segment_name)] = df_cum_agg

    stats_df["exp_id"] = exp_id
    stats_df["client"] = client
    stats_df["segment"] = segment_name
    stats_df_tot[(client, segment_name)] = stats_df

    is_results_exists = execute_sql(f"exists {cfg.exp_results_table}")
    if int(is_results_exists.iloc[0].values[0]) == 0:
        create_exp_results_table(stats_df, config=cfg)
    else:
        drop_exp_partitions(exp_id, client_name=client, segment=segment_name, table_name="ug_exp_results", config=cfg)
        update_exp_results_table(stats_df, table="ug_exp_results", config=cfg)

    is_stats_exists = execute_sql(f"exists {cfg.exp_stats_table}")
    if int(is_stats_exists.iloc[0].values[0]) == 0:
        create_exp_stats_table(df_cum_agg, config=cfg)
    else:
        drop_exp_partitions(exp_id, client_name=client, segment=segment_name, table_name="ug_exp_stats", config=cfg)
        update_exp_results_table(df_cum_agg, table="ug_exp_stats", config=cfg)

    return subscription_table


def calculate_exp_info(
    exp_id,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
    update_rollout: bool = True,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, pd.DataFrame], str]:
    cfg = config or ExperimentCalculatorConfig.from_env()
    exp_info = get_experiment(exp_id, config=cfg)
    funnels_config = load_funnels_config(cfg.funnels_yaml_path)

    if not exp_info.get("clients_list"):
        exp_info["clients_list"] = list(cfg.default_clients)
    if exp_info.get("experiment_event_start") in [None, "", "xxx"]:
        raise ValueError(f"Experiment {exp_id} has invalid experiment_event_start: {exp_info.get('experiment_event_start')}")

    if cfg.update_subscription_sources:
        logger.info("Updating subscription source tables")
        update_subscription_source_tables(config=cfg)

    df_tot = {}
    stats_df_tot = {}
    df_cum_agg_tot = {}
    retention_cache = {}
    tab_view_cache = {}
    product_metrics_segments = {}
    exp_users_table = ""
    subscription_table = ""

    for client in exp_info["clients_list"]:
        for segment_name, segment in exp_info["segments"].items():
            segment_hash = get_experiment_users_hash(exp_info, client, segment)
            logger.info("Calculating experiment info for exp_id=%s, client=%s, segment=%s", exp_id, client, segment_name)
            logger.info("Experiment info:\n%s", exp_info)

            logger.info("Loading users")
            exp_users_table = create_experiment_users_table(exp_info, client, segment_name, segment, config=cfg)
            segment_items = [(segment_name, segment, segment_hash)]
            segment_items.extend(
                create_experiment_users_slice_segments(
                    exp_info,
                    exp_users_table,
                    client,
                    segment_name,
                    segment,
                    segment_hash,
                    config=cfg,
                )
            )

            for current_segment_name, current_segment, current_segment_hash in segment_items:
                logger.info(
                    "Calculating segment metrics for exp_id=%s, client=%s, segment=%s",
                    exp_id,
                    client,
                    current_segment_name,
                )
                subscription_table = _calculate_exp_segment_info(
                    exp_info=exp_info,
                    funnels_config=funnels_config,
                    exp_users_table=exp_users_table,
                    client=client,
                    segment_name=current_segment_name,
                    segment=current_segment,
                    segment_hash=current_segment_hash,
                    df_tot=df_tot,
                    df_cum_agg_tot=df_cum_agg_tot,
                    stats_df_tot=stats_df_tot,
                    retention_cache=retention_cache,
                    tab_view_cache=tab_view_cache,
                    product_metrics_segments=product_metrics_segments,
                    config=cfg,
                )

    if update_rollout:
        logger.info("Updating rollout split users for exp_id=%s, clients=%s", exp_id, exp_info["clients_list"])
        update_rollout_split_users_daily(exp_info, exp_info["clients_list"], config=cfg)
        logger.info("Finished updating rollout split users for exp_id=%s", exp_id)

    return df_tot, df_cum_agg_tot, stats_df_tot, f"exp_users_table={exp_users_table}, subscription_table={subscription_table}"
