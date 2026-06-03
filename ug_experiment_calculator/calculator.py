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
)
from .repository import (
    create_exp_funnel_results_table,
    create_exp_funnel_stats_table,
    create_exp_results_table,
    create_exp_stats_table,
    create_experiment_users_table,
    create_experiments_subscription_table,
    drop_exp_partitions,
    drop_table,
    get_experiment,
    get_monetization_metrics,
    get_segment_hash,
    get_tour_subscription_funnels,
    update_exp_results_table,
    update_subscription_source_tables,
)


logger = logging.getLogger(__name__)


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
) -> None:
    is_exists = execute_sql(f"exists {full_table_name}")
    if int(is_exists.iloc[0].values[0]) == 0:
        if df.empty:
            logger.info("Skipping %s creation: dataframe is empty", full_table_name)
            return
        create_table_func(df, config=config)
        return

    drop_exp_partitions(exp_id, client_name=client, segment=segment_name, table_name=logical_table_name, config=config)
    if df.empty:
        logger.info("Skipping %s insert: dataframe is empty", full_table_name)
        return

    update_exp_results_table(df, table=logical_table_name, config=config)


def calculate_exp_info(
    exp_id,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, pd.DataFrame], str]:
    cfg = config or ExperimentCalculatorConfig.from_env()
    exp_info = get_experiment(exp_id, config=cfg)

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
    exp_users_table = ""
    subscription_table = ""

    for client in exp_info["clients_list"]:
        for segment_name, segment in exp_info["segments"].items():
            segment_hash = get_segment_hash(segment)
            logger.info("Calculating experiment info for exp_id=%s, client=%s, segment=%s", exp_id, client, segment_name)
            logger.info("Experiment info:\n%s", exp_info)

            logger.info("Loading users")
            exp_users_table = create_experiment_users_table(exp_info, client, segment_name, segment, config=cfg)

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
            df_tot[(client, segment_name)] = df

            logger.info("Loading subscription funnels")
            funnel_df = get_tour_subscription_funnels(
                exp_users_table,
                subscription_table,
                client,
                segment_name,
                segment_hash,
                config=cfg,
            )

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
            )

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

    return df_tot, df_cum_agg_tot, stats_df_tot, f"exp_users_table={exp_users_table}, subscription_table={subscription_table}"
