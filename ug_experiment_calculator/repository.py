from __future__ import annotations

import ast
import datetime
import hashlib
import json
import logging
import math
import random
import re
import string
import textwrap
from typing import Optional

from clickhouse_worker import (
    ClickHouseQueryError,
    clickhouse_string_literal as _clickhouse_string_literal,
    create_client,
    execute_sql,
    execute_sql_modify,
    insert_dataframe,
    pandas_to_clickhouse_types,
)
import numpy as np
import pandas as pd
import yaml

from .config import ExperimentCalculatorConfig


logger = logging.getLogger(__name__)
SUBSCRIPTION_SOURCE_VERSION = 8
EXPERIMENT_USERS_CACHE_VERSION = 5
TRIAL_CONVERSION_MODEL_TABLE = "trial_conversion_model"
EXPERIMENT_OUTPUT_UPDATED_AT_COLUMNS = {
    "updated_at": "DateTime",
}

UG_WEB_CLIENT = "UG_WEB"
UG_WEB_DESKTOP_CLIENT = "UG_WEB DESKTOP"
UG_WEB_MOBWEB_CLIENT = "UG_WEB MOBWEB"
UG_WEB_CALCULATION_CLIENTS = {UG_WEB_DESKTOP_CLIENT, UG_WEB_MOBWEB_CLIENT}


def get_config(config: Optional[ExperimentCalculatorConfig] = None) -> ExperimentCalculatorConfig:
    return config or ExperimentCalculatorConfig.from_env()


def generate_random_id(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def get_query(query_name: str, params: Optional[dict] = None, *, config: Optional[ExperimentCalculatorConfig] = None) -> str:
    cfg = get_config(config)
    sql_req = (cfg.queries_dir / f"{query_name}.sql").read_text(encoding="utf-8")
    return sql_req.format(**params) if params else sql_req


def create_table_sql(
    table_name: str,
    *,
    schema: str,
    partition: str,
    sorting: str,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    cfg = get_config(config)
    return get_query(
        "create_table_template",
        params={
            "full_table_name": cfg.full_table(table_name),
            "cluster": cfg.cluster,
            "zookeeper_path": cfg.zookeeper_path(table_name),
            "schema": schema,
            "partition": partition,
            "sorting": sorting,
        },
        config=cfg,
    )


def create_transient_table_sql(
    table_name: str,
    *,
    schema: str,
    partition: str,
    sorting: str,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    cfg = get_config(config)
    return get_query(
        "create_transient_table_template",
        params={
            "full_table_name": cfg.full_table(table_name),
            "cluster": cfg.cluster,
            "schema": schema,
            "partition": partition,
            "sorting": sorting,
        },
        config=cfg,
    )


def experiment_base_id(exp_info: dict) -> int:
    return int(exp_info.get("base_id", exp_info["id"]))


def experiment_storage_id(exp_info: dict) -> str:
    return _identifier_part(exp_info.get("storage_id", exp_info.get("exp_launch_id", exp_info["id"])))


def experiment_launch_id(exp_info: dict) -> str:
    return str(exp_info.get("exp_launch_id", experiment_storage_id(exp_info)))


def experiment_output_exp_id(exp_info: dict) -> int:
    return int(exp_info.get("output_exp_id", experiment_base_id(exp_info)))


def drop_exp_partitions(
    exp_id: int,
    client_name: str,
    segment: str,
    table_name: str = "ug_exp_results",
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> None:
    cfg = get_config(config)
    table = cfg.physical_table(table_name)

    partitions_sql = f"""
    SELECT DISTINCT
        partition
    FROM clusterAllReplicas('{cfg.cluster}', system.parts)
    WHERE database = '{cfg.database}'
      AND table = '{table}'
      AND active
      AND partition LIKE '%,{exp_id},''{client_name}'',''{segment}'')'
    ORDER BY partition
    """

    client = create_client()
    try:
        partitions = client.query(partitions_sql).result_rows

        if not partitions:
            logger.info(
                "No active partitions found for exp_id=%s, client=%s, segment=%s, table=%s",
                exp_id,
                client_name,
                segment,
                table,
            )
            return

        for (partition,) in partitions:
            year_month, partition_exp_id, partition_client_name, partition_segment = partition.strip("()").split(",")

            drop_sql = f"""
            ALTER TABLE {cfg.database}.{table}
            ON CLUSTER {cfg.cluster}
            DROP PARTITION ({year_month}, {partition_exp_id}, {partition_client_name}, {partition_segment})
            """

            logger.info(
                "Drop partition: (%s, %s, %s, %s)",
                year_month,
                partition_exp_id,
                partition_client_name,
                partition_segment,
            )
            client.command(drop_sql)
    except ValueError as exc:
        raise ClickHouseQueryError(f"Invalid response: {exc}") from exc
    except Exception as exc:
        raise ClickHouseQueryError(f"Unexpected error: {exc}") from exc
    finally:
        client.close()


def prepare_df_for_clickhouse(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    string_columns = [
        "dt",
        "metric",
        "funnel_definition_key",
        "funnel_definition_name",
        "funnel_definition_description",
        "funnel_key",
        "funnel_name",
        "transition_key",
        "transition_name",
        "from_step_key",
        "from_step_name",
        "to_step_key",
        "to_step_name",
        "variation_pair",
        "numerator",
        "denominator",
        "variance",
        "distribution",
        "percentage",
        "client",
        "segment",
        "segment_hash",
        "exp_launch_id",
    ]

    int_columns = [
        "control_variation",
        "test_variation",
        "exp_id",
        "base_exp_id",
        "variation",
        "from_step_order",
        "to_step_order",
        "control_denominator",
        "control_numerator",
        "test_denominator",
        "test_numerator",
        "denominator_users",
        "numerator_users",
        "members",
        "install_cnt",
        "app_referral_tour_cnt",
        "subscriber_cnt",
        "otp_owner_cnt",
        "access_owner_cnt",
        "access_instant_cnt",
        "access_ex_trial_cnt",
        "access_trial_cnt",
        "access_intro_cnt",
        "trial_subscriber_cnt",
        "active_trial_cnt",
        "access_otp_cnt",
        "subscriptions_cnt",
        "access_cnt",
        "charged_trial_cnt",
        "expected_trial_cnt",
        "any_charged_trial_cnt",
        "active_charged_trial_cnt",
        "cancel_trial_cnt",
        "trial_buyer_cnt",
        "late_charged_cnt",
        "subscribe_buyer_cnt",
        "buyer_cnt",
        "subscription_charge_cnt",
        "charge_cnt",
        "refund_14d_cnt",
        "recurrent_charge_cnt",
        "upgrade_cnt",
        "upgrade_revenue",
        "cancel_14d_cnt",
        "cancel_1m_cnt",
        "web_retention_1d_cnt",
        "web_retention_7d_cnt",
        "web_retention_14d_cnt",
        "app_retention_1d_cnt",
        "app_retention_7d_cnt",
        "app_retention_14d_cnt",
        "mobweb_app_retention_1d_cnt",
        "mobweb_app_retention_7d_cnt",
        "mobweb_app_retention_14d_cnt",
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
    ]

    float_columns = [
        "mean_0",
        "mean_1",
        "mean_diff",
        "ci_low",
        "ci_high",
        "pvalue",
        "lift",
        "revenue",
        "refund_revenue",
        "recurrent_revenue",
        "trial_revenue",
        "expected_charged_trial_cnt",
        "expected_revenue",
        "active_trial_revenue",
        "lifetime_revenue",
        "arpu_var",
        "lifetime_arpu_var",
        "arppu_var",
        "subscriptions_per_user_var",
        "intros_per_user_var",
        "charges_per_user_var",
        "web_tab_view_events_per_user_var",
        "web_tab_view_60s_events_per_user_var",
        "web_tab_view_120s_events_per_user_var",
        "web_tab_view_180s_events_per_user_var",
        "web_tab_view_300s_events_per_user_var",
        "web_tab_view_600s_events_per_user_var",
        "app_tab_view_events_per_user_var",
        "app_tab_view_60s_events_per_user_var",
        "app_tab_view_120s_events_per_user_var",
        "app_tab_view_180s_events_per_user_var",
        "app_tab_view_300s_events_per_user_var",
        "app_tab_view_600s_events_per_user_var",
        "mobweb_app_tab_view_events_per_user_var",
        "mobweb_app_tab_view_60s_events_per_user_var",
        "mobweb_app_tab_view_120s_events_per_user_var",
        "mobweb_app_tab_view_180s_events_per_user_var",
        "mobweb_app_tab_view_300s_events_per_user_var",
        "mobweb_app_tab_view_600s_events_per_user_var",
        "value",
        "conversion",
    ]

    for col in string_columns:
        if col in df.columns:
            df[col] = df[col].replace({np.nan: ""}).fillna("").astype(str)

    for col in int_columns:
        if col in df.columns:
            df[col] = df[col].replace({np.nan: 0}).fillna(0).astype("int64")

    for col in float_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    if "updated_at" in df.columns:
        df["updated_at"] = pd.to_datetime(df["updated_at"], errors="coerce").astype("datetime64[ns]")

    return df


def with_output_updated_at(df: pd.DataFrame, updated_at: Optional[datetime.datetime] = None) -> pd.DataFrame:
    df = df.copy()
    if updated_at is None:
        updated_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    elif updated_at.tzinfo is not None:
        updated_at = updated_at.astimezone(datetime.timezone.utc).replace(tzinfo=None)

    df["updated_at"] = pd.Series([updated_at] * len(df), index=df.index, dtype="datetime64[ns]")
    return df


def insert_df_by_chunks(table_name: str, df: pd.DataFrame, chunk_size: int = 1000) -> None:
    prepared_df = prepare_df_for_clickhouse(df)
    total = len(prepared_df)

    for start in range(0, total, chunk_size):
        end = min(start + chunk_size, total)
        chunk = prepared_df.iloc[start:end].copy()
        logger.info("Insert rows %s - %s / %s into %s", start, end, total, table_name)
        insert_dataframe(table_name, chunk)


def parse_configuration_project(row) -> str:
    text = str(row)
    project = ""

    match_project = re.search(r'project:\s*"?([^",\s]+)"?', text)
    if match_project:
        project = match_project.group(1)
    else:
        match_url = re.search(r"https?://[^\s,\"]+", text)
        if match_url:
            project = match_url.group(0)

    if project:
        project = project.split("#")[0]

    return project


def parse_configuration_segments(row) -> dict:
    default_segments = {"Total": {"pro_rights": "All"}}
    text = str(row)
    if not text:
        return default_segments

    full_config = _parse_configuration_value(text)
    parsed_segments = _normalize_segments(full_config.get("segments") if isinstance(full_config, dict) else None)
    if parsed_segments:
        return _with_total_segment(parsed_segments)

    segments_text = _extract_balanced_config_value(text, "segments")
    if segments_text:
        parsed_segments = _normalize_segments(_parse_configuration_value(segments_text))
        if parsed_segments:
            return _with_total_segment(parsed_segments)

    segments_text = _extract_yaml_block_value(text, "segments")
    if segments_text:
        parsed_segments = _normalize_segments(_parse_configuration_value(segments_text))
        if parsed_segments:
            return _with_total_segment(parsed_segments)

    return default_segments


def _parse_configuration_value(text: str):
    for parser in (yaml.safe_load, ast.literal_eval):
        try:
            value = parser(text)
        except Exception:
            continue
        if value is not None:
            return value
    return None


def _normalize_segments(value) -> dict:
    if not isinstance(value, dict):
        return {}

    result = {}
    for segment_name, segment_config in value.items():
        if not segment_name:
            continue

        if segment_config is None:
            segment_config = {}
        if not isinstance(segment_config, dict):
            continue

        result[str(segment_name)] = segment_config
    return result


def _with_total_segment(segments: dict) -> dict:
    if "Total" in segments:
        return segments
    return {"Total": {"pro_rights": "All"}, **segments}


def _extract_balanced_config_value(text: str, key: str) -> str:
    start_match = re.search(rf"\b{re.escape(key)}\s*[:=]\s*", text)
    if not start_match:
        return ""

    start = start_match.end()
    while start < len(text) and text[start].isspace():
        start += 1

    if start >= len(text) or text[start] not in "{[":
        return ""

    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    quote_char = ""
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = bool(quote_char)
            continue
        if quote_char:
            if char == quote_char:
                quote_char = ""
            continue
        if char in {"'", '"'}:
            quote_char = char
            continue
        if char == opener:
            depth += 1
            continue
        if char == closer:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]

    return ""


def _extract_yaml_block_value(text: str, key: str) -> str:
    lines = text.splitlines()
    for line_index, line in enumerate(lines):
        match = re.match(rf"^(\s*){re.escape(key)}\s*:\s*$", line)
        if not match:
            continue

        base_indent = len(match.group(1))
        block_lines = []
        for next_line in lines[line_index + 1:]:
            if not next_line.strip():
                block_lines.append(next_line)
                continue

            current_indent = len(next_line) - len(next_line.lstrip())
            if current_indent <= base_indent:
                break

            block_lines.append(next_line)

        return textwrap.dedent("\n".join(block_lines)).strip()

    return ""


def get_exps_list(domain: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> list[int]:
    query = get_query("get_ug_exps_ids_to_calc", params={"domain": domain}, config=config)
    df = execute_sql(query)
    return df["id"].tolist()


def get_ugm_exps_list(*, config: Optional[ExperimentCalculatorConfig] = None) -> list[int]:
    return get_exps_list("UG Monetization", config=config)


def get_ugp_exps_list(*, config: Optional[ExperimentCalculatorConfig] = None) -> list[int]:
    return get_exps_list("UG Product", config=config)


def get_ugg_exps_list(*, config: Optional[ExperimentCalculatorConfig] = None) -> list[int]:
    return get_exps_list("UG Growth", config=config)


def get_experiment(id, *, config: Optional[ExperimentCalculatorConfig] = None) -> dict:
    query = get_query("get_ug_exp_info", params={"id": id}, config=config)
    df = execute_sql(query)
    clients_pattern = r"(\w+)"
    df["clients_list"] = df.clients.apply(lambda x: re.findall(clients_pattern, x))
    exp_info = {
        "id": df.id[0],
        "date_start": df.date_start[0],
        "date_end": df.date_end[0],
        "variations": df.variations[0],
        "experiment_event_start": df.experiment_event_start[0],
        "configuration": df.configuration[0],
        "clients_list": df.clients_list[0],
        "clients_options": df.clients_options[0],
        "name": df.name[0],
    }
    exp_info["project"] = parse_configuration_project(exp_info["configuration"])
    exp_info["segments"] = parse_configuration_segments(exp_info["configuration"])

    logger.info("exp_info: %s", exp_info)
    return exp_info


def get_experiment_launches(exp_info: dict, *, config: Optional[ExperimentCalculatorConfig] = None) -> list[dict]:
    base_id = experiment_base_id(exp_info)
    query = f"""
        select
            `event_id`,
            `date_created`
        from `mysql_u_guitarcom`.`ab_experiment_history`
        where
            `experiment_id` = {base_id}
        and
            `event_id` in (5, 6)
        order by
            `date_created`,
            `id`
    """
    df = execute_sql(query)
    if df.empty:
        return [_with_experiment_launch_context(exp_info, exp_info["date_start"], exp_info.get("date_end", 0), 1, True)]

    intervals = []
    current_start = None
    for row in df.itertuples(index=False):
        event_id = int(row.event_id)
        date_created = int(row.date_created)
        if event_id == 5:
            if current_start is not None and date_created > current_start:
                intervals.append((current_start, date_created - 1))
            current_start = date_created
            continue

        if event_id == 6 and current_start is not None:
            intervals.append((current_start, date_created))
            current_start = None

    if current_start is not None:
        date_end = int(exp_info.get("date_end", 0) or 0)
        intervals.append((current_start, date_end if date_end > current_start else 0))

    if not intervals:
        return [_with_experiment_launch_context(exp_info, exp_info["date_start"], exp_info.get("date_end", 0), 1, True)]

    intervals = sorted(intervals, key=lambda item: item[0])
    launches = []
    for launch_number, (date_start, date_end) in enumerate(intervals, start=1):
        launches.append(
            _with_experiment_launch_context(
                exp_info,
                date_start,
                date_end,
                launch_number,
                launch_number == len(intervals),
            )
        )
    return launches


def get_experiment_client_contexts(exp_info: dict, *, config: Optional[ExperimentCalculatorConfig] = None) -> list[dict]:
    base_id = experiment_base_id(exp_info)
    launch_start = int(exp_info.get("date_start", 0) or 0)
    launch_end = int(exp_info.get("date_end", 0) or 0)
    history_rows = _get_experiment_history_rows(base_id)
    if history_rows.empty:
        return _current_experiment_client_contexts(exp_info)

    state = _initial_client_history_state(exp_info)
    for row in history_rows.itertuples(index=False):
        date_created = int(row.date_created)
        if date_created > launch_start:
            break
        _apply_client_history_attrs(state, _parse_history_attributes(row.experiment_attributes))

    contexts_by_client: dict[str, dict] = {}
    segment_start = launch_start
    history_max_date = int(history_rows["date_created"].max() or 0)
    current_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    boundary_end = launch_end if launch_end > launch_start else max(current_ts, history_max_date)
    relevant_rows = [
        row
        for row in history_rows.itertuples(index=False)
        if launch_start < int(row.date_created) <= boundary_end
    ]

    for row in relevant_rows:
        date_created = int(row.date_created)
        if date_created > segment_start:
            _add_client_contexts_from_state(contexts_by_client, exp_info, state, segment_start, date_created - 1)
        _apply_client_history_attrs(state, _parse_history_attributes(row.experiment_attributes))
        segment_start = date_created

    final_end = launch_end if launch_end > launch_start else 0
    _add_client_contexts_from_state(contexts_by_client, exp_info, state, segment_start, final_end)

    if not contexts_by_client:
        return _current_experiment_client_contexts(exp_info)

    launch_clients = list(exp_info.get("clients_list") or [])
    ordered_clients = []
    for client in [*launch_clients, *contexts_by_client.keys()]:
        if client not in ordered_clients and client in contexts_by_client:
            ordered_clients.append(client)

    return [contexts_by_client[client] for client in ordered_clients]


def get_experiment_clients(
    exp_info: dict,
    clients: list[str] | tuple[str, ...] | None = None,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> list[str]:
    cfg = get_config(config)
    base_exp_info = dict(exp_info)
    if not base_exp_info.get("clients_list"):
        base_exp_info["clients_list"] = list(cfg.default_clients)

    if clients is None:
        return _unique_ordered_client_context_values(get_experiment_client_contexts(base_exp_info, config=cfg))

    requested_clients = [str(client) for client in clients]
    requested_expanded = expand_experiment_clients(base_exp_info, requested_clients)
    requested_lookup = set(requested_clients) | set(requested_expanded)

    context_clients = []
    for client_info in get_experiment_client_contexts(base_exp_info, config=cfg):
        context_client = str(client_info["clients_list"][0])
        if context_client in requested_lookup or base_client_for_calculation(context_client) in requested_lookup:
            context_clients.append(context_client)

    result = []
    for client in [*requested_expanded, *context_clients]:
        if client not in result:
            result.append(client)
    return result


def _unique_ordered_client_context_values(client_contexts: list[dict]) -> list[str]:
    result = []
    for client_info in client_contexts:
        client = str(client_info["clients_list"][0])
        if client not in result:
            result.append(client)
    return result


def _get_experiment_history_rows(exp_id: int) -> pd.DataFrame:
    query = f"""
        select
            `event_id`,
            `date_created`,
            `experiment_attributes`
        from `mysql_u_guitarcom`.`ab_experiment_history`
        where
            `experiment_id` = {exp_id}
        order by
            `date_created`,
            `id`
    """
    return execute_sql(query)


def _current_experiment_client_contexts(exp_info: dict) -> list[dict]:
    result = []
    for client in exp_info.get("clients_list") or []:
        client_info = dict(exp_info)
        client_info["clients_list"] = [client]
        result.append(client_info)
    return result


def _initial_client_history_state(exp_info: dict) -> dict:
    return {
        "clients": list(exp_info.get("clients_list") or []),
        "clients_options": exp_info.get("clients_options", ""),
    }


def _parse_history_attributes(value: object) -> dict:
    if isinstance(value, dict):
        return value
    parsed = _parse_configuration_value(str(value))
    return parsed if isinstance(parsed, dict) else {}


def _history_clients(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return re.findall(r"(\w+)", value)
    if isinstance(value, (list, tuple, set)):
        return [str(client) for client in value if str(client)]
    return None


def _apply_client_history_attrs(state: dict, attrs: dict) -> None:
    clients = _history_clients(attrs.get("clients"))
    if clients is not None:
        state["clients"] = clients
    if "clients_options" in attrs:
        state["clients_options"] = attrs.get("clients_options") or ""


def _add_client_contexts_from_state(
    contexts_by_client: dict[str, dict],
    exp_info: dict,
    state: dict,
    date_start: int,
    date_end: int,
) -> None:
    if date_end and date_end < date_start:
        return

    state_info = dict(exp_info)
    state_info["clients_list"] = list(state.get("clients") or [])
    state_info["clients_options"] = state.get("clients_options", "")
    expanded_clients = expand_experiment_clients(state_info)
    for client in expanded_clients:
        previous = contexts_by_client.get(client)
        client_info = dict(state_info)
        client_info["clients_list"] = [client]
        client_info["date_start"] = min(int(previous["date_start"]), date_start) if previous else int(date_start)
        client_info["date_end"] = _merge_client_context_end(
            previous.get("date_end") if previous else None,
            date_end,
        )
        contexts_by_client[client] = client_info


def _merge_client_context_end(previous_end: object, next_end: int) -> int:
    if previous_end is None:
        return int(next_end or 0)
    previous_end_int = int(previous_end or 0)
    next_end_int = int(next_end or 0)
    if previous_end_int == 0 or next_end_int == 0:
        return 0
    return max(previous_end_int, next_end_int)


def _with_experiment_launch_context(
    exp_info: dict,
    date_start: int,
    date_end: int,
    launch_number: int,
    is_latest_launch: bool,
) -> dict:
    base_id = experiment_base_id(exp_info)
    launch_id = str(base_id) if is_latest_launch else f"{base_id}_launch_{launch_number}"
    output_exp_id = base_id if is_latest_launch else -(base_id * 1000 + launch_number)
    launch_info = dict(exp_info)
    launch_info.update(
        {
            "id": base_id,
            "base_id": base_id,
            "date_start": int(date_start),
            "date_end": int(date_end or 0),
            "storage_id": launch_id,
            "exp_launch_id": launch_id,
            "launch_number": launch_number,
            "is_latest_launch": is_latest_launch,
            "output_exp_id": output_exp_id,
        }
    )
    return launch_info


def generate_sql_rights_filter(rights_type: str, rights: str) -> str:
    rights_level_list = ["pro", "edu", "sing", "practice", "book"]
    rights_level = int(math.pow(10, rights_level_list.index(rights_type)))
    rights_dict = {
        "empty": f"toUInt32(rights / {rights_level}) % 10 = 0",
        "free": f"toUInt32(rights / {rights_level}) % 10 in (0, 4, 5)",
        "finite subscription": f"toUInt32(rights / {rights_level}) % 10 in (1, 2)",
        "lifetime": f"toUInt32(rights / {rights_level}) % 10 in (3)",
        "any paid": f"toUInt32(rights / {rights_level}) % 10 in (2, 3)",
        "any subscription": f"toUInt32(rights / {rights_level}) % 10 in (1, 2, 3)",
        "trial": f"toUInt32(rights / {rights_level}) % 10 in (1)",
        "expired subscription": f"toUInt32(rights / {rights_level}) % 10 in (5)",
        "expired trial": f"toUInt32(rights / {rights_level}) % 10 in (4)",
        "expired any": f"toUInt32(rights / {rights_level}) % 10 in (4, 5)",
        "all": "1",
    }
    return rights_dict[rights]


def _parse_clients_options(clients_options: object):
    if isinstance(clients_options, str):
        parsed = _parse_configuration_value(clients_options)
        return parsed if parsed is not None else clients_options
    return clients_options


def _collect_platform_values(options: object) -> list[object]:
    if isinstance(options, dict):
        values = []
        for key, value in options.items():
            if str(key).lower() == "platform":
                values.extend(_flatten_option_values(value))
            else:
                values.extend(_collect_platform_values(value))
        return values

    if isinstance(options, (list, tuple, set)):
        items = list(options)
        if len(items) == 2 and str(items[0]).lower() == "platform":
            return _flatten_option_values(items[1])
        values = []
        for value in options:
            values.extend(_collect_platform_values(value))
        return values

    return []


def _flatten_option_values(value: object) -> list[object]:
    if isinstance(value, dict):
        values = []
        for nested_value in value.values():
            values.extend(_flatten_option_values(nested_value))
        return values
    if isinstance(value, (list, tuple, set)):
        values = []
        for item in value:
            values.extend(_flatten_option_values(item))
        return values
    return [value]


def base_client_for_calculation(client: str) -> str:
    return UG_WEB_CLIENT if str(client) in UG_WEB_CALCULATION_CLIENTS else str(client)


def source_client_for_calculation(client: str) -> str:
    return base_client_for_calculation(client)


def _client_options_from_exp_info(exp_info: dict, client: str) -> object:
    parsed_options = _parse_clients_options(exp_info.get("clients_options", ""))
    if isinstance(parsed_options, dict):
        return parsed_options.get(base_client_for_calculation(client), {})
    return {}


def _platform_bucket_flags(platform_values: list[object]) -> tuple[bool, bool]:
    has_desktop = False
    has_mobweb = False
    for value in platform_values:
        text = str(value).strip().lower()
        if text in {"all"}:
            has_desktop = True
            has_mobweb = True
            continue
        if text in {"desktop", "web"}:
            has_desktop = True
            continue
        if text in {"mobile", "mobweb", "mobile_web", "mobile web", "mweb", "phone", "tablet"}:
            has_mobweb = True
            continue
        try:
            platform_id = int(text)
        except ValueError:
            continue
        if platform_id == 1:
            has_desktop = True
        elif platform_id > 1:
            has_mobweb = True
    return has_desktop, has_mobweb


def is_mixed_web_experiment(exp_info: dict) -> bool:
    if UG_WEB_CLIENT not in exp_info.get("clients_list", []):
        return False
    client_options = _client_options_from_exp_info(exp_info, UG_WEB_CLIENT)
    platform_values = _collect_platform_values(client_options)
    if not platform_values:
        return True
    has_desktop, has_mobweb = _platform_bucket_flags(platform_values)
    return has_desktop and has_mobweb


def expand_experiment_clients(exp_info: dict, clients: list[str] | tuple[str, ...] | None = None) -> list[str]:
    source_clients = list(clients if clients is not None else exp_info.get("clients_list") or [])
    if not is_mixed_web_experiment(exp_info):
        return source_clients

    result = []
    for client in source_clients:
        client_text = str(client)
        if client_text == UG_WEB_CLIENT:
            candidates = [UG_WEB_DESKTOP_CLIENT, UG_WEB_MOBWEB_CLIENT]
        else:
            candidates = [client_text]
        for candidate in candidates:
            if candidate not in result:
                result.append(candidate)
    return result


def _text_has_mobweb_marker(value: object) -> bool:
    text = str(value).lower()
    return (
        "mobweb" in text
        or "mobile_web" in text
        or "mobile web" in text
        or "platform > 1" in text
        or "platform>1" in text
    )


def _platform_values_are_mobweb(platform_values: list[object]) -> bool:
    if not platform_values:
        return True

    for value in platform_values:
        if isinstance(value, (int, float)):
            if int(value) > 1:
                return True
            continue

        text = str(value).strip().lower()
        if text in {"mobweb", "mobile_web", "mobile web", "mweb"}:
            return True
        try:
            if int(text) > 1:
                return True
        except ValueError:
            continue

    return False


def is_mobweb_segment(segment: dict, clients_options: object = "", client: str = "UG_WEB") -> bool:
    if str(client) == UG_WEB_MOBWEB_CLIENT:
        return True
    if str(client) == UG_WEB_DESKTOP_CLIENT:
        return False

    if "platform" in segment:
        has_desktop, has_mobweb = _platform_bucket_flags(_flatten_option_values(segment.get("platform")))
        if has_mobweb:
            return True
        if has_desktop:
            return False

    platform = str(segment.get("platform", "")).lower()
    if platform in {"mobweb", "mobile_web", "mobile web", "mweb"}:
        return True
    if platform in {"desktop", "web"}:
        return False
    try:
        if int(platform) == 1:
            return False
    except ValueError:
        pass
    if segment.get("mobweb") is True or segment.get("mobile_web") is True:
        return True

    segment_sql = json.dumps(segment, sort_keys=True, ensure_ascii=True, default=str).lower()
    if "platform > 1" in segment_sql or "platform>1" in segment_sql:
        return True

    parsed_options = _parse_clients_options(clients_options)
    if isinstance(parsed_options, dict):
        parsed_options = parsed_options.get(base_client_for_calculation(client), {})
    if _text_has_mobweb_marker(parsed_options):
        return True
    return _platform_values_are_mobweb(_collect_platform_values(parsed_options))


def exp_raw_data_query_name(client: str, segment: dict, *, clients_options: object = "", insert: bool = False) -> str:
    suffix = "_insert" if insert else ""
    if base_client_for_calculation(client) == UG_WEB_CLIENT:
        if is_mobweb_segment(segment, clients_options, client):
            return f"exp_raw_data_mobweb{suffix}"
        return f"exp_raw_data_web{suffix}"
    return f"exp_raw_data_app{suffix}"


def web_event_platform_filter_sql(client: str, segment: dict, clients_options: object = "") -> str:
    if base_client_for_calculation(client) != UG_WEB_CLIENT:
        return "1"
    if is_mobweb_segment(segment, clients_options, client):
        return "`platform` > 1"
    return "`platform` = 1"


def app_product_sample_params(
    client: str,
    segment: dict,
    clients_options: object = "",
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> tuple[str, str]:
    if base_client_for_calculation(client) == UG_WEB_CLIENT and is_mobweb_segment(segment, clients_options, client):
        cfg = get_config(config)
        sample_rate = float(cfg.mobweb_product_metrics_sample_rate)
        if sample_rate <= 0:
            raise ValueError("mobweb_product_metrics_sample_rate must be greater than 0")
        if sample_rate >= 1:
            return "1", "1"

        bucket_count = 10000
        threshold = max(1, min(bucket_count, round(sample_rate * bucket_count)))
        multiplier = bucket_count / threshold
        return f"cityHash64(toUInt64(`eut`.`unified_id`)) % {bucket_count} < {threshold}", f"{multiplier:.12g}"
    return "1", "1"


def _stable_config_hash(config: object) -> str:
    config_json = json.dumps(config, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str)
    return hashlib.sha256(config_json.encode("utf-8")).hexdigest()


def _experiment_time_params(exp_info: dict) -> dict[str, int]:
    return {
        "exp_start_ts": int(exp_info.get("date_start", 0) or 0),
        "exp_end_ts": int(exp_info.get("date_end", 0) or 0),
    }


def get_segment_hash(segment: dict, *, exp_info: Optional[dict] = None, client: str = "") -> str:
    if exp_info is None:
        return _stable_config_hash(segment)

    return _stable_config_hash(_experiment_users_hash_config(exp_info, client, segment))


def get_user_filters_hash(segment: dict, *, client: str = "", clients_options: object = "") -> str:
    user_filter_segment = {
        "query": exp_raw_data_query_name(client, segment, clients_options=clients_options),
        "uwf": segment.get("uwf", "1"),
        "uhf": segment.get("uhf", "1"),
        "pro_rights": str(segment.get("pro_rights", "all")).lower(),
        "edu_rights": str(segment.get("edu_rights", "all")).lower(),
        "sing_rights": str(segment.get("sing_rights", "all")).lower(),
        "practice_rights": str(segment.get("practice_rights", "all")).lower(),
        "book_rights": str(segment.get("book_rights", "all")).lower(),
        "platform": segment.get("platform", ""),
        "mobweb": segment.get("mobweb", False),
        "mobile_web": segment.get("mobile_web", False),
        "slice": segment.get("slice", ""),
    }
    segment_json = json.dumps(user_filter_segment, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str)
    return hashlib.sha256(segment_json.encode("utf-8")).hexdigest()


def _experiment_users_hash_config(exp_info: dict, client: str, segment: dict) -> dict:
    clients_options = exp_info.get("clients_options", "")
    return {
        "cache_version": EXPERIMENT_USERS_CACHE_VERSION,
        "user_filters_hash": get_user_filters_hash(segment, client=client, clients_options=clients_options),
        "client": client,
        "clients_options": clients_options,
        "date_start": int(exp_info.get("date_start", 0) or 0),
        "date_end": int(exp_info.get("date_end", 0) or 0),
        "experiment_event_start": exp_info.get("experiment_event_start", ""),
    }


def get_experiment_users_hash(exp_info: dict, client: str, segment: dict) -> str:
    return get_segment_hash(segment, exp_info=exp_info, client=client)


def get_segment_slice_field(segment: dict) -> str:
    slice_config = segment.get("slice", "")
    if isinstance(slice_config, str):
        return slice_config.strip()
    if isinstance(slice_config, dict):
        return str(slice_config.get("field") or "").strip()
    return ""


EXP_USERS_COLUMNS = (
    "unified_id",
    "variation",
    "exp_start_dt",
    "rights",
    "user_id",
    "payment_account_id",
    "country",
    "auth",
    "client",
    "segment",
    "segment_hash",
    "app_unified_id",
    "has_app",
    "subscription_unified_ids",
    "os",
    "browser",
    "frontend_release_version",
    "backend_release_version",
    "web_version",
    "platform",
    "type",
    "is_new",
    "connection",
    "device_manufacturer",
)


MOBWEB_WEB_USERS_SCHEMA = """
(
    `unified_id` Int64,
    `variation` UInt32,
    `exp_start_dt` UInt32,
    `rights` Int64,
    `user_id` Int64,
    `country` String,
    `auth` UInt8,
    `os` String,
    `browser` String,
    `frontend_release_version` Array(UInt32),
    `backend_release_version` Array(UInt32),
    `web_version` Array(UInt32),
    `platform` Int64,
    `type` String,
    `is_new` UInt8,
    `connection` String,
    `device_manufacturer` String
)
"""

MOBWEB_WEB_INSTALLS_SCHEMA = """
(
    `unified_id` Int64,
    `variation` UInt32,
    `install_payment_account_id` UInt64,
    `install_dt` DateTime
)
"""

MOBWEB_APP_USERS_SCHEMA = """
(
    `unified_id` Int64,
    `variation` UInt32,
    `app_unified_id` Int64,
    `app_payment_account_id` UInt64,
    `app_start_dt` DateTime
)
"""


def _exp_users_insert_columns_sql() -> str:
    return ", ".join(f"`{column}`" for column in EXP_USERS_COLUMNS)


def _exp_users_insert_prefix(table_name: str) -> str:
    return f"""
        insert into {table_name} ({_exp_users_insert_columns_sql()})
        settings insert_deduplicate = 0
    """


def _quoted_identifier(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def _wrap_exp_users_query(query: str, client: str, segment_name: str, segment_hash: str) -> str:
    return f"""
        select
            `unified_id`,
            `variation`,
            `exp_start_dt`,
            `rights`,
            `user_id`,
            `payment_account_id`,
            `country`,
            `auth`,
            {_clickhouse_string_literal(client)} as `client`,
            {_clickhouse_string_literal(segment_name)} as `segment`,
            {_clickhouse_string_literal(segment_hash)} as `segment_hash`,
            `app_unified_id`,
            `has_app`,
            `subscription_unified_ids`,
            `os`,
            `browser`,
            `frontend_release_version`,
            `backend_release_version`,
            `web_version`,
            `platform`,
            `type`,
            `is_new`,
            `connection`,
            `device_manufacturer`
        from (
            {query}
        )
    """


def _insert_into_table_from_select(
    full_table_name: str,
    query_name: str,
    params: dict,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> None:
    query = "insert into " + full_table_name + "\n" + get_query(query_name, params=params, config=config)
    logger.info("Inserting into %s with query:\n%s", full_table_name, query)
    execute_sql_modify(query)


def _identifier_part(value: object) -> str:
    text = re.sub(r"[^0-9a-zA-Z_]+", "_", str(value)).strip("_").lower()
    return text or "empty"


def _mobweb_stage_table_name(storage_id: str, client: str, segment_hash: str, current_day: datetime.datetime, stage: str) -> str:
    return (
        f"exp_users_{storage_id}_mobweb_"
        f"{_identifier_part(stage)}_"
        f"{_identifier_part(client)}_"
        f"{segment_hash[:12]}_"
        f"{current_day.strftime('%Y%m%d')}"
    )


def _recreate_mobweb_stage_table(
    table_name: str,
    query_name: str,
    params: dict,
    *,
    schema: str,
    partition: str,
    sorting: str,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    cfg = get_config(config)
    full_table_name = cfg.full_table(table_name)
    drop_table(full_table_name, config=cfg)
    query = create_transient_table_sql(table_name, schema=schema, partition=partition, sorting=sorting, config=cfg)
    logger.info("Creating transient table %s with query:\n%s", full_table_name, query)
    execute_sql_modify(query)
    _insert_into_table_from_select(full_table_name, query_name, params, config=cfg)
    return full_table_name


def _create_mobweb_stage_table(
    table_name: str,
    *,
    schema: str,
    partition: str,
    sorting: str,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    cfg = get_config(config)
    full_table_name = cfg.full_table(table_name)
    drop_table(full_table_name, config=cfg)
    query = create_transient_table_sql(table_name, schema=schema, partition=partition, sorting=sorting, config=cfg)
    logger.info("Creating transient table %s with query:\n%s", full_table_name, query)
    execute_sql_modify(query)
    return full_table_name


def _insert_mobweb_experiment_users_day(
    full_table_name: str,
    exp_info: dict,
    client: str,
    segment_name: str,
    segment_hash: str,
    current_day: datetime.datetime,
    where_filter: str,
    having_filter: str,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> None:
    cfg = get_config(config)
    exp_id = experiment_base_id(exp_info)
    storage_id = experiment_storage_id(exp_info)
    source_client = source_client_for_calculation(client)
    common_params = {
        "exp_id": exp_id,
        "where_sql": where_filter,
        "having_sql": having_filter,
        "date_filter": current_day.strftime("%Y-%m-%d"),
        "exp_users_table": full_table_name,
        "client": source_client,
        "client_sql": _clickhouse_string_literal(client),
        "segment_sql": _clickhouse_string_literal(segment_name),
        "segment_hash_sql": _clickhouse_string_literal(segment_hash),
    } | _experiment_time_params(exp_info)

    web_users_table = _recreate_mobweb_stage_table(
        _mobweb_stage_table_name(storage_id, client, segment_hash, current_day, "web_users"),
        "exp_raw_data_mobweb_web_users",
        common_params,
        schema=MOBWEB_WEB_USERS_SCHEMA,
        partition="toYYYYMM(toDate(exp_start_dt))",
        sorting="unified_id, variation",
        config=cfg,
    )
    web_installs_params = common_params | {"web_users_table": web_users_table}
    web_installs_table = _recreate_mobweb_stage_table(
        _mobweb_stage_table_name(storage_id, client, segment_hash, current_day, "web_installs"),
        "exp_raw_data_mobweb_web_installs",
        web_installs_params,
        schema=MOBWEB_WEB_INSTALLS_SCHEMA,
        partition="toYYYYMM(toDate(install_dt))",
        sorting="unified_id, variation",
        config=cfg,
    )
    app_users_table = _create_mobweb_stage_table(
        _mobweb_stage_table_name(storage_id, client, segment_hash, current_day, "app_users"),
        schema=MOBWEB_APP_USERS_SCHEMA,
        partition="toYYYYMM(toDate(app_start_dt))",
        sorting="unified_id, variation",
        config=cfg,
    )
    exp_end_dt = datetime.datetime.now(datetime.timezone.utc)
    if exp_info["date_end"] > exp_info["date_start"]:
        exp_end_dt = datetime.datetime.fromtimestamp(exp_info["date_end"], datetime.timezone.utc)
    app_date = current_day.date()
    while app_date <= exp_end_dt.date():
        app_users_params = common_params | {
            "web_installs_table": web_installs_table,
            "app_date_filter": app_date.strftime("%Y-%m-%d"),
        }
        logger.info("Inserting mobweb app users for app_date=%s", app_date)
        _insert_into_table_from_select(app_users_table, "exp_raw_data_mobweb_app_users", app_users_params, config=cfg)
        app_date += datetime.timedelta(days=1)

    final_params = common_params | {
        "web_users_table": web_users_table,
        "web_installs_table": web_installs_table,
        "app_users_table": app_users_table,
    }
    query_part_2 = get_query("exp_raw_data_mobweb_insert", params=final_params, config=cfg)
    query = _exp_users_insert_prefix(full_table_name)
    query += "\n" + _wrap_exp_users_query(query_part_2, client, segment_name, segment_hash)
    logger.info("Inserting final mobweb experiment users table with query:\n%s", query)
    execute_sql_modify(query)


def _should_insert_exp_users_day(
    table_name: str,
    current_day: datetime.datetime,
    client: str,
    segment_name: str,
    segment_hash: str,
) -> bool:
    current_day_str = current_day.strftime("%Y-%m-%d")
    query = f"""
        select
            countIf(toDate(`exp_start_dt`, 'UTC') = toDate('{current_day_str}')) as `rows_for_day`,
            max(toDate(`exp_start_dt`, 'UTC')) as `max_dt`
        from {table_name}
        where
            `client` = {_clickhouse_string_literal(client)}
        and
            `segment` = {_clickhouse_string_literal(segment_name)}
        and
            `segment_hash` = {_clickhouse_string_literal(segment_hash)}
    """
    df = execute_sql(query)
    rows_for_day = int(df["rows_for_day"].iloc[0] or 0)
    max_dt = df["max_dt"].iloc[0]

    if rows_for_day == 0:
        return True
    if pd.isna(max_dt):
        return True

    return str(max_dt)[:10] == current_day_str


def _add_months(source_date: datetime.date, months: int) -> datetime.date:
    month = source_date.month - 1 + months
    year = source_date.year + month // 12
    month = month % 12 + 1
    day = min(
        source_date.day,
        [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1],
    )
    return datetime.date(year, month, day)


def _iter_half_year_blocks(date_start: datetime.date, date_end: datetime.date):
    block_start = date_start
    while block_start <= date_end:
        next_block_start = _add_months(block_start, 6)
        block_end = min(next_block_start - datetime.timedelta(days=1), date_end)
        yield block_start, block_end
        block_start = next_block_start


def _get_table_max_subscribed_date(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> Optional[datetime.date]:
    cfg = get_config(config)
    is_exists = execute_sql(f"exists {table_name}")
    if int(is_exists.iloc[0].values[0]) == 0:
        return None

    df = execute_sql(f"select max(toDate(`subscribed_dt`)) as `max_dt` from {table_name}")
    max_dt = df["max_dt"].iloc[0]
    if pd.isna(max_dt):
        return None
    if isinstance(max_dt, datetime.datetime):
        max_dt = max_dt.date()
    elif not isinstance(max_dt, datetime.date):
        max_dt = datetime.datetime.strptime(str(max_dt)[:10], "%Y-%m-%d").date()

    if max_dt < cfg.subscriptions_start_date:
        return None

    return max_dt


def _table_has_column(table_name: str, column_name: str) -> bool:
    database, short_table_name = table_name.split(".", 1)
    query = f"""
        select count() as `columns_cnt`
        from system.columns
        where
            `database` = '{database}'
        and
            `table` = '{short_table_name}'
        and
            `name` = '{column_name}'
    """
    df = execute_sql(query)
    return int(df["columns_cnt"].iloc[0] or 0) > 0


def _ensure_segment_hash_column(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> bool:
    cfg = get_config(config)
    if _table_has_column(table_name, "segment_hash"):
        return False

    query = f"""
        alter table {table_name}
        on cluster {cfg.cluster}
        add column if not exists `segment_hash` String default ''
    """
    execute_sql_modify(query)
    return True


def _ensure_exp_users_extra_columns(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> bool:
    cfg = get_config(config)
    columns_added = False
    columns = {
        "app_unified_id": "Int64 default 0",
        "has_app": "UInt8 default 0",
        "subscription_unified_ids": "Array(Int64) default []",
        "os": "String default ''",
        "browser": "String default ''",
        "frontend_release_version": "Array(UInt32) default []",
        "backend_release_version": "Array(UInt32) default []",
        "web_version": "Array(UInt32) default []",
        "platform": "Int64 default 0",
        "type": "String default ''",
        "is_new": "UInt8 default 0",
        "connection": "String default ''",
        "device_manufacturer": "String default ''",
    }
    for column_name, column_type in columns.items():
        if _table_has_column(table_name, column_name):
            continue

        query = f"""
            alter table {table_name}
            on cluster {cfg.cluster}
            add column if not exists `{column_name}` {column_type}
        """
        execute_sql_modify(query)
        columns_added = True
    return columns_added


def _delete_exp_users_segment(
    table_name: str,
    client: str,
    segment_name: str,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> None:
    cfg = get_config(config)
    query = f"""
        alter table {table_name}
        on cluster {cfg.cluster}
        delete where
            `client` = {_clickhouse_string_literal(client)}
        and
            `segment` = {_clickhouse_string_literal(segment_name)}
        settings mutations_sync = 1
    """
    logger.info("Deleting cached users from %s for client=%s, segment=%s", table_name, client, segment_name)
    execute_sql_modify(query)


def delete_experiment_users_segment(
    exp_id: int,
    client: str,
    segment_name: str,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> None:
    cfg = get_config(config)
    table_name = cfg.full_table(f"exp_users_{int(exp_id)}")
    exists_df = execute_sql(f"exists {table_name}")
    if int(exists_df.iloc[0].values[0]) == 0:
        return
    _delete_exp_users_segment(table_name, client, segment_name, config=cfg)


def cleanup_obsolete_experiment_segments(
    exp_info: dict,
    exp_users_table: str,
    client: str,
    active_segment_names: set[str],
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> None:
    cfg = get_config(config)
    if not active_segment_names:
        return

    exists_df = execute_sql(f"exists {exp_users_table}")
    if int(exists_df.iloc[0].values[0]) == 0:
        return

    query = f"""
        select distinct
            `segment`
        from {exp_users_table}
        where
            `client` = {_clickhouse_string_literal(client)}
    """
    df = execute_sql(query)
    existing_segments = {str(segment) for segment in df["segment"].dropna().tolist()}
    obsolete_segments = sorted(existing_segments - set(active_segment_names))
    if not obsolete_segments:
        return

    output_exp_id = experiment_output_exp_id(exp_info)
    for segment_name in obsolete_segments:
        logger.info(
            "Cleaning obsolete experiment segment for exp_id=%s, client=%s, segment=%s",
            output_exp_id,
            client,
            segment_name,
        )
        _delete_exp_users_segment(exp_users_table, client, segment_name, config=cfg)
        for table_name in (
            "ug_exp_results",
            "ug_exp_stats",
            "ug_exp_funnel_results",
            "ug_exp_funnel_stats",
        ):
            drop_exp_partitions(
                output_exp_id,
                client_name=client,
                segment=segment_name,
                table_name=table_name,
                config=cfg,
            )


def _ensure_exp_users_segment_hash(
    table_name: str,
    client: str,
    segment_name: str,
    segment_hash: str,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> None:
    columns_added = _ensure_segment_hash_column(table_name, config=config)
    columns_added = _ensure_exp_users_extra_columns(table_name, config=config) or columns_added
    query = f"""
        select
            count() as `rows_cnt`,
            countIf(`segment_hash` != {_clickhouse_string_literal(segment_hash)}) as `mismatched_rows_cnt`
        from {table_name}
        where
            `client` = {_clickhouse_string_literal(client)}
        and
            `segment` = {_clickhouse_string_literal(segment_name)}
    """
    df = execute_sql(query)
    rows_cnt = int(df["rows_cnt"].iloc[0] or 0)
    mismatched_rows_cnt = int(df["mismatched_rows_cnt"].iloc[0] or 0)
    if columns_added and rows_cnt > 0:
        logger.info(
            "Experiment users schema changed for client=%s, segment=%s: deleting %s cached rows",
            client,
            segment_name,
            rows_cnt,
        )
        _delete_exp_users_segment(table_name, client, segment_name, config=config)
        return

    if rows_cnt == 0 or mismatched_rows_cnt == 0:
        return

    logger.info(
        "Segment hash changed for client=%s, segment=%s: deleting %s cached rows",
        client,
        segment_name,
        rows_cnt,
    )
    _delete_exp_users_segment(table_name, client, segment_name, config=config)


def _was_subscription_day_updated_recently(table_name: str, subscribed_date: datetime.date) -> bool:
    if not _table_has_column(table_name, "updated_at"):
        return False

    query = f"""
        select max(`updated_at`) as `last_updated_at`
        from {table_name}
        where toDate(`subscribed_dt`) = toDate('{subscribed_date}')
    """
    df = execute_sql(query)
    last_updated_at = df["last_updated_at"].iloc[0]
    if pd.isna(last_updated_at):
        return False

    if not isinstance(last_updated_at, datetime.datetime):
        last_updated_at = datetime.datetime.strptime(str(last_updated_at)[:19], "%Y-%m-%d %H:%M:%S")
    if last_updated_at.tzinfo is None:
        last_updated_at = last_updated_at.replace(tzinfo=datetime.timezone.utc)

    return last_updated_at >= datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)


def _ensure_updated_at_column(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    cfg = get_config(config)
    if _table_has_column(table_name, "updated_at"):
        return

    query = f"""
        alter table {table_name}
        on cluster {cfg.cluster}
        add column if not exists `updated_at` DateTime default toDateTime(0)
    """
    execute_sql_modify(query)


def _ensure_next_subscribed_dt_column(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> bool:
    cfg = get_config(config)
    if _table_has_column(table_name, "next_subscribed_dt"):
        return False

    query = f"""
        alter table {table_name}
        on cluster {cfg.cluster}
        add column if not exists `next_subscribed_dt` UInt32 default toUInt32(4102444800) after `subscribed_dt`
    """
    execute_sql_modify(query)
    return True


def _ensure_payment_account_id_vector_column(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> bool:
    cfg = get_config(config)
    if _table_has_column(table_name, "payment_account_id_vector"):
        return False

    query = f"""
        alter table {table_name}
        on cluster {cfg.cluster}
        add column if not exists `payment_account_id_vector` Array(UInt32) default [] after `payment_account_id`
    """
    execute_sql_modify(query)
    return True


def _ensure_is_access_intro_column(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> bool:
    cfg = get_config(config)
    if _table_has_column(table_name, "is_access_intro"):
        return False

    query = f"""
        alter table {table_name}
        on cluster {cfg.cluster}
        add column if not exists `is_access_intro` UInt8 default 0 after `duration_count`
    """
    execute_sql_modify(query)
    return True


def _ensure_subscription_event_detail_columns(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> bool:
    cfg = get_config(config)
    changed = False
    columns = {
        "base_price": "Float64 default 0 after `product_id`",
        "country": "String default '' after `base_price`",
    }

    for column_name, column_definition in columns.items():
        if _table_has_column(table_name, column_name):
            continue

        query = f"""
            alter table {table_name}
            on cluster {cfg.cluster}
            add column if not exists `{column_name}` {column_definition}
        """
        execute_sql_modify(query)
        changed = True

    return changed


def _ensure_source_version_column(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> bool:
    cfg = get_config(config)
    if _table_has_column(table_name, "source_version"):
        return False

    query = f"""
        alter table {table_name}
        on cluster {cfg.cluster}
        add column if not exists `source_version` UInt16 default toUInt16(0) after `updated_at`
    """
    execute_sql_modify(query)
    return True


def _has_stale_source_version(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> bool:
    if not _table_has_column(table_name, "source_version"):
        return True

    query = f"""
        select 1 as `has_stale_source_version`
        from {table_name}
        where `source_version` != toUInt16({SUBSCRIPTION_SOURCE_VERSION})
        limit 1
    """
    return not execute_sql(query).empty


def _create_table_from_select(
    table_name: str,
    query_name: str,
    params: dict,
    partition: str,
    sorting: str,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> None:
    cfg = get_config(config)
    create_query = create_table_sql(table_name, schema="", partition=partition, sorting=sorting, config=cfg)
    select_query = get_query(query_name, params=params, config=cfg)
    query = create_query + "\n as \n select * from (\n" + select_query + "\n) where 0"
    logger.info("Creating table %s with query:\n%s", cfg.full_table(table_name), query)
    execute_sql_modify(query)


def _delete_subscriptions_block(table_name: str, block_start: datetime.date, block_end: datetime.date, *, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    cfg = get_config(config)
    query = f"""
        alter table {table_name}
        on cluster {cfg.cluster}
        delete where toDate(`subscribed_dt`) between toDate('{block_start}') and toDate('{block_end}')
        settings mutations_sync = 1
    """
    logger.info("Deleting subscriptions block from %s for %s - %s", table_name, block_start, block_end)
    execute_sql_modify(query)


def _ensure_subscription_source_tables(*, config: Optional[ExperimentCalculatorConfig] = None) -> bool:
    cfg = get_config(config)
    needs_full_refresh = False

    is_subscriptions_exists = execute_sql(f"exists {cfg.subscriptions_table}")
    if int(is_subscriptions_exists.iloc[0].values[0]) == 0:
        _create_table_from_select(
            "subscriptions",
            "subscriptions_store_by_sub_date",
            {
                "date_start": cfg.subscriptions_start_date.strftime("%Y-%m-%d"),
                "date_end": cfg.subscriptions_start_date.strftime("%Y-%m-%d"),
            },
            "toYYYYMM(toDate(subscribed_dt))",
            "subscribed_dt, subscription_id, product_code",
            config=cfg,
        )
    else:
        _ensure_updated_at_column(cfg.subscriptions_table, config=cfg)
        needs_full_refresh = _ensure_next_subscribed_dt_column(cfg.subscriptions_table, config=cfg)
        needs_full_refresh = _ensure_payment_account_id_vector_column(cfg.subscriptions_table, config=cfg) or needs_full_refresh
        needs_full_refresh = _ensure_is_access_intro_column(cfg.subscriptions_table, config=cfg) or needs_full_refresh
        needs_full_refresh = _ensure_subscription_event_detail_columns(cfg.subscriptions_table, config=cfg) or needs_full_refresh
        needs_full_refresh = _ensure_source_version_column(cfg.subscriptions_table, config=cfg) or needs_full_refresh
        needs_full_refresh = _has_stale_source_version(cfg.subscriptions_table, config=cfg) or needs_full_refresh

    is_transactions_exists = execute_sql(f"exists {cfg.subscription_transactions_table}")
    if int(is_transactions_exists.iloc[0].values[0]) == 0:
        _create_table_from_select(
            "subscriptions_transactions",
            "subscription_transactions_store_by_sub_date",
            {
                "date_start": cfg.subscriptions_start_date.strftime("%Y-%m-%d"),
                "date_end": cfg.subscriptions_start_date.strftime("%Y-%m-%d"),
                "subscriptions_table": cfg.subscriptions_table,
            },
            "toYYYYMM(toDate(subscribed_dt))",
            "subscribed_dt, subscription_id, product_code",
            config=cfg,
        )
    else:
        _ensure_updated_at_column(cfg.subscription_transactions_table, config=cfg)
        needs_full_refresh = _ensure_source_version_column(cfg.subscription_transactions_table, config=cfg) or needs_full_refresh
        needs_full_refresh = _has_stale_source_version(cfg.subscription_transactions_table, config=cfg) or needs_full_refresh

    return needs_full_refresh


def _get_table_max_update_date(table_name: str) -> Optional[datetime.date]:
    query = f"""
        select max(`update_dt`) as `max_update_dt`
        from {table_name}
    """
    df = execute_sql(query)
    max_update_dt = df["max_update_dt"].iloc[0]
    if pd.isna(max_update_dt):
        return None
    if isinstance(max_update_dt, datetime.datetime):
        return max_update_dt.date()
    if isinstance(max_update_dt, datetime.date):
        return max_update_dt
    return datetime.datetime.strptime(str(max_update_dt)[:10], "%Y-%m-%d").date()


def update_trial_conversion_model(*, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    cfg = get_config(config)
    table_name = TRIAL_CONVERSION_MODEL_TABLE
    full_table_name = cfg.full_table(table_name)
    today = datetime.datetime.now(datetime.timezone.utc).date()

    is_exists = execute_sql(f"exists {full_table_name}")
    if int(is_exists.iloc[0].values[0]) == 0:
        _create_table_from_select(
            table_name,
            "trial_conversion_model",
            {"update_dt": today.strftime("%Y-%m-%d")},
            "`update_dt`",
            "`platform`, `tier`, `base_price_int`",
            config=cfg,
        )
    else:
        ensure_table_columns(
            table_name,
            {
                "update_dt": "Date",
                "base_price_label": "String",
                "n_trials": "UInt64",
                "n_converted": "UInt64",
                "conversion": "Float64",
            },
            config=cfg,
        )
        max_update_dt = _get_table_max_update_date(full_table_name)
        if max_update_dt is not None and max_update_dt >= today:
            logger.info("Skipping trial conversion model update: %s already has update_dt=%s", full_table_name, max_update_dt)
            return

    query = get_query("trial_conversion_model", {"update_dt": today.strftime("%Y-%m-%d")}, config=cfg)
    logger.info("Updating trial conversion model %s for update_dt=%s", full_table_name, today)
    execute_sql_modify(f"insert into {full_table_name}\n{query}")


def update_subscription_source_tables(*, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    cfg = get_config(config)
    needs_full_refresh = _ensure_subscription_source_tables(config=cfg)

    subscriptions_max_dt = _get_table_max_subscribed_date(cfg.subscriptions_table, config=cfg)
    transactions_max_dt = _get_table_max_subscribed_date(cfg.subscription_transactions_table, config=cfg)
    dates = [dt for dt in [subscriptions_max_dt, transactions_max_dt] if dt is not None]
    date_start = cfg.subscriptions_start_date if needs_full_refresh else min(dates) if dates else cfg.subscriptions_start_date
    date_end = datetime.datetime.now(datetime.timezone.utc).date()

    if date_start > date_end:
        return

    if (
        not needs_full_refresh
        and date_start == date_end
        and _was_subscription_day_updated_recently(cfg.subscriptions_table, date_start)
        and _was_subscription_day_updated_recently(cfg.subscription_transactions_table, date_start)
    ):
        logger.info("Skipping subscription source tables update for %s: updated less than 1 hour ago", date_start)
        return

    for block_start, block_end in _iter_half_year_blocks(date_start, date_end):
        logger.info("Updating subscription source tables for %s - %s", block_start, block_end)

        _delete_subscriptions_block(cfg.subscription_transactions_table, block_start, block_end, config=cfg)
        _delete_subscriptions_block(cfg.subscriptions_table, block_start, block_end, config=cfg)

        subscriptions_query = get_query(
            "subscriptions_store_by_sub_date",
            {
                "date_start": block_start.strftime("%Y-%m-%d"),
                "date_end": block_end.strftime("%Y-%m-%d"),
            },
            config=cfg,
        )
        execute_sql_modify(f"insert into {cfg.subscriptions_table}\n{subscriptions_query}")

        transactions_query = get_query(
            "subscription_transactions_store_by_sub_date",
            {
                "date_start": block_start.strftime("%Y-%m-%d"),
                "date_end": block_end.strftime("%Y-%m-%d"),
                "subscriptions_table": cfg.subscriptions_table,
            },
            config=cfg,
        )
        execute_sql_modify(f"insert into {cfg.subscription_transactions_table}\n{transactions_query}")


def create_experiment_users_table(
    exp_info: dict,
    client: str,
    segment_name: str,
    segment: dict,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    cfg = get_config(config)
    exp_id = experiment_base_id(exp_info)
    exp_start_dt = datetime.datetime.fromtimestamp(exp_info["date_start"], datetime.timezone.utc)
    table_name = f"exp_users_{experiment_storage_id(exp_info)}"
    full_table_name = cfg.full_table(table_name)
    segment_hash = get_experiment_users_hash(exp_info, client, segment)
    source_client = source_client_for_calculation(client)
    is_web_client = base_client_for_calculation(client) == UG_WEB_CLIENT
    is_mobweb = is_web_client and is_mobweb_segment(segment, exp_info.get("clients_options", ""), client)

    where_filter = segment.get("uwf", "1")
    if exp_info["experiment_event_start"] == "App Experiment Start":
        where_filter += f" and (event = 'App Experiment Start' and item_id = {exp_id})"
    elif exp_info["experiment_event_start"] != "":
        where_filter += f" and event = '{exp_info['experiment_event_start']}'"
    having_filter = segment.get("uhf", "1")
    pro_rights = generate_sql_rights_filter("pro", segment.get("pro_rights", "all").lower())
    edu_rights = generate_sql_rights_filter("edu", segment.get("edu_rights", "all").lower())
    sing_rights = generate_sql_rights_filter("edu", segment.get("sing_rights", "all").lower())
    practice_rights = generate_sql_rights_filter("edu", segment.get("practice_rights", "all").lower())
    book_rights = generate_sql_rights_filter("edu", segment.get("book_rights", "all").lower())
    having_filter += f" and ({pro_rights} and {edu_rights} and {sing_rights} and {practice_rights} and {book_rights})"

    is_exists = execute_sql(f"exists {full_table_name}")
    if int(is_exists.iloc[0].values[0]) == 0:
        query_part_1 = create_table_sql(
            table_name,
            schema="",
            partition="toYYYYMM(toDate(exp_start_dt)), client, segment",
            sorting="client, segment, segment_hash, exp_start_dt",
            config=cfg,
        )
        seed_query_name = (
            "exp_raw_data_mobweb_schema"
            if is_mobweb
            else exp_raw_data_query_name(client, segment, clients_options=exp_info.get("clients_options", ""))
        )
        seed_query = get_query(
            seed_query_name,
            params={
                "exp_id": exp_id,
                "where_sql": where_filter,
                "having_sql": having_filter,
                "date_filter": exp_start_dt.strftime("%Y-%m-%d"),
                "client": source_client,
            }
            | _experiment_time_params(exp_info),
            config=cfg,
        )
        query_part_2 = _wrap_exp_users_query(seed_query, client, segment_name, segment_hash)
        query = query_part_1 + "\n as \n select * from (\n" + query_part_2 + "\n) where 0"
        logger.info("Creating experiment users table with query:\n%s", query)
        execute_sql_modify(query)

    _ensure_exp_users_segment_hash(full_table_name, client, segment_name, segment_hash, config=cfg)

    exp_end_dt = datetime.datetime.now(datetime.timezone.utc)
    if exp_info["date_end"] > exp_info["date_start"]:
        exp_end_dt = datetime.datetime.fromtimestamp(exp_info["date_end"], datetime.timezone.utc)
    days_cnt = (exp_end_dt.date() - exp_start_dt.date()).days
    for day in range(days_cnt + 1):
        current_day = exp_start_dt + datetime.timedelta(days=day)
        if not _should_insert_exp_users_day(full_table_name, current_day, client, segment_name, segment_hash):
            logger.info(
                "Skipping users insert for exp_id=%s, client=%s, segment=%s, date=%s",
                exp_id,
                client,
                segment_name,
                current_day.strftime("%Y-%m-%d"),
            )
            continue

        if is_mobweb:
            _insert_mobweb_experiment_users_day(
                full_table_name,
                exp_info,
                client,
                segment_name,
                segment_hash,
                current_day,
                where_filter,
                having_filter,
                config=cfg,
            )
            continue

        query_part_1 = _exp_users_insert_prefix(full_table_name)
        insert_query_name = exp_raw_data_query_name(
            client,
            segment,
            clients_options=exp_info.get("clients_options", ""),
            insert=True,
        )
        query_part_2 = get_query(
            insert_query_name,
            params={
                "exp_id": exp_id,
                "where_sql": where_filter,
                "having_sql": having_filter,
                "date_filter": current_day.strftime("%Y-%m-%d"),
                "exp_users_table": full_table_name,
                "client": source_client,
                "client_sql": _clickhouse_string_literal(client),
                "segment_sql": _clickhouse_string_literal(segment_name),
                "segment_hash_sql": _clickhouse_string_literal(segment_hash),
            }
            | _experiment_time_params(exp_info),
            config=cfg,
        )
        query = query_part_1 + "\n" + _wrap_exp_users_query(query_part_2, client, segment_name, segment_hash)
        logger.info("Inserting experiment users table with query:\n%s", query)
        execute_sql_modify(query)

    return full_table_name


def create_experiment_users_slice_segments(
    exp_info: dict,
    exp_users_table: str,
    client: str,
    base_segment_name: str,
    base_segment: dict,
    base_segment_hash: str,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> list[tuple[str, dict, str]]:
    cfg = get_config(config)
    slice_field = get_segment_slice_field(base_segment)
    if not slice_field:
        return []
    if not _table_has_column(exp_users_table, slice_field):
        raise ValueError(f"Segment {base_segment_name!r} slice field {slice_field!r} does not exist in {exp_users_table}")

    slice_identifier = _quoted_identifier(slice_field)
    slice_values_query = f"""
        select distinct
            toString({slice_identifier}) as `slice_value`
        from {exp_users_table}
        where
            `client` = {_clickhouse_string_literal(client)}
        and
            `segment` = {_clickhouse_string_literal(base_segment_name)}
        and
            `segment_hash` = {_clickhouse_string_literal(base_segment_hash)}
        and
            not empty(toString({slice_identifier}))
        order by
            `slice_value`
    """
    slice_values_df = execute_sql(slice_values_query)
    slice_values = [str(value) for value in slice_values_df["slice_value"].dropna().tolist()]

    derived_segments = []
    for slice_value in slice_values:
        derived_segment_name = f"{base_segment_name} - {slice_value}"
        derived_segment = dict(base_segment)
        derived_segment["slice"] = {
            "field": slice_field,
            "value": slice_value,
        }
        derived_segment_hash = get_experiment_users_hash(exp_info, client, derived_segment)
        _ensure_exp_users_segment_hash(exp_users_table, client, derived_segment_name, derived_segment_hash, config=cfg)
        _insert_experiment_users_slice_segment(
            exp_users_table,
            client,
            base_segment_name,
            base_segment_hash,
            derived_segment_name,
            derived_segment_hash,
            slice_field,
            slice_value,
            config=cfg,
        )
        derived_segments.append((derived_segment_name, derived_segment, derived_segment_hash))

    return derived_segments


def _insert_experiment_users_slice_segment(
    exp_users_table: str,
    client: str,
    base_segment_name: str,
    base_segment_hash: str,
    derived_segment_name: str,
    derived_segment_hash: str,
    slice_field: str,
    slice_value: str,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> None:
    cfg = get_config(config)
    slice_identifier = _quoted_identifier(slice_field)
    base_days_query = f"""
        select distinct
            toDate(`exp_start_dt`, 'UTC') as `dt`
        from {exp_users_table}
        where
            `client` = {_clickhouse_string_literal(client)}
        and
            `segment` = {_clickhouse_string_literal(base_segment_name)}
        and
            `segment_hash` = {_clickhouse_string_literal(base_segment_hash)}
        and
            toString({slice_identifier}) = {_clickhouse_string_literal(slice_value)}
        order by
            `dt`
    """
    base_days_df = execute_sql(base_days_query)
    for current_date in base_days_df["dt"].tolist():
        current_day = _to_datetime_utc(current_date)
        if not _should_insert_exp_users_day(exp_users_table, current_day, client, derived_segment_name, derived_segment_hash):
            logger.info(
                "Skipping users slice insert for client=%s, segment=%s, slice=%s, date=%s",
                client,
                derived_segment_name,
                slice_value,
                current_day.strftime("%Y-%m-%d"),
            )
            continue

        select_columns = []
        for column in EXP_USERS_COLUMNS:
            if column == "client":
                select_columns.append(f"{_clickhouse_string_literal(client)} as `client`")
            elif column == "segment":
                select_columns.append(f"{_clickhouse_string_literal(derived_segment_name)} as `segment`")
            elif column == "segment_hash":
                select_columns.append(f"{_clickhouse_string_literal(derived_segment_hash)} as `segment_hash`")
            else:
                select_columns.append(f"`base`.{_quoted_identifier(column)}")
        select_columns_sql = ",\n            ".join(select_columns)
        query = f"""
            {_exp_users_insert_prefix(exp_users_table)}
            select
                {select_columns_sql}
            from
                {exp_users_table} as `base`
            left join
                {exp_users_table} as `existing`
            on
                `base`.`unified_id` = `existing`.`unified_id`
            and
                `base`.`variation` = `existing`.`variation`
            and
                `existing`.`client` = {_clickhouse_string_literal(client)}
            and
                `existing`.`segment` = {_clickhouse_string_literal(derived_segment_name)}
            and
                `existing`.`segment_hash` = {_clickhouse_string_literal(derived_segment_hash)}
            where
                `base`.`client` = {_clickhouse_string_literal(client)}
            and
                `base`.`segment` = {_clickhouse_string_literal(base_segment_name)}
            and
                `base`.`segment_hash` = {_clickhouse_string_literal(base_segment_hash)}
            and
                toDate(`base`.`exp_start_dt`, 'UTC') = toDate('{current_day.strftime("%Y-%m-%d")}')
            and
                toString(`base`.{slice_identifier}) = {_clickhouse_string_literal(slice_value)}
            and
                `existing`.`unified_id` = 0
        """
        logger.info(
            "Inserting experiment users slice segment client=%s, base_segment=%s, segment=%s, slice=%s, date=%s",
            client,
            base_segment_name,
            derived_segment_name,
            slice_value,
            current_day.strftime("%Y-%m-%d"),
        )
        execute_sql_modify(query)


def _to_datetime_utc(value: object) -> datetime.datetime:
    if isinstance(value, datetime.datetime):
        return value.astimezone(datetime.timezone.utc)
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day, tzinfo=datetime.timezone.utc)
    return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)


def create_experiments_subscription_table(
    exp_info: dict,
    client: str,
    segment: dict,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    cfg = get_config(config)
    _ensure_subscription_source_tables(config=cfg)
    session_id = generate_random_id(32)
    table_name = f"exp_subscription_{experiment_storage_id(exp_info)}_{session_id}"
    query_part_1 = create_transient_table_sql(
        table_name,
        schema="",
        partition="toYYYYMM(toDate(subscribed_dt))",
        sorting="subscribed_dt",
        config=cfg,
    )
    where_filter = segment.get("swf", "1")
    having_filter = segment.get("shf", "1")
    exp_start_dt = datetime.datetime.fromtimestamp(exp_info["date_start"], datetime.timezone.utc)
    exp_end_dt = datetime.datetime.now(datetime.timezone.utc)
    if exp_info["date_end"] > exp_info["date_start"]:
        exp_end_dt = datetime.datetime.fromtimestamp(exp_info["date_end"], datetime.timezone.utc)
    query_part_2 = get_query(
        "subscriptions_joined_by_sub_date",
        params={
            "date_start": exp_start_dt.strftime("%Y-%m-%d"),
            "date_end": exp_end_dt.strftime("%Y-%m-%d"),
            "where_sql": where_filter,
            "having_sql": having_filter,
            "subscriptions_table": cfg.subscriptions_table,
            "transactions_table": cfg.subscription_transactions_table,
        },
        config=cfg,
    )
    query = query_part_1 + "\n as \n" + query_part_2
    execute_sql_modify(query)

    return cfg.full_table(table_name)


def drop_table(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    cfg = get_config(config)
    query = f"""
        drop table if exists {table_name} on cluster {cfg.cluster}
        settings
        distributed_ddl_task_timeout = 0,
        distributed_ddl_output_mode = 'none'
    """
    execute_sql_modify(query)


def get_monetization_metrics(
    exp_info: dict,
    exp_users_table: str,
    subscription_table: str,
    client: str,
    segment_name: str,
    segment_hash: str = "",
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    cfg = get_config(config)
    query = get_query(
        "monetization_metrics",
        params={
            "exp_users_table": exp_users_table,
            "subscription_table": subscription_table,
            "client_sql": _clickhouse_string_literal(client),
            "segment_sql": _clickhouse_string_literal(segment_name),
            "segment_hash_sql": _clickhouse_string_literal(segment_hash),
            "trial_conversion_model_table": cfg.full_table(TRIAL_CONVERSION_MODEL_TABLE),
        },
        config=cfg,
    )
    logger.info("total query:\n%s", query)
    return execute_sql(query)


def get_retention_metrics(
    exp_users_table: str,
    client: str,
    segment_name: str,
    segment_hash: str = "",
    *,
    calculate_app_retention: bool = True,
    segment: Optional[dict] = None,
    clients_options: object = "",
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    is_web_client = base_client_for_calculation(client) == UG_WEB_CLIENT
    calculate_web_retention = is_web_client and not is_mobweb_segment(segment or {}, clients_options, client)
    web_event_platform_sql = web_event_platform_filter_sql(client, segment or {}, clients_options)
    app_product_sample_sql, app_product_sample_multiplier_sql = app_product_sample_params(
        client,
        segment or {},
        clients_options,
        config=config,
    )
    query = get_query(
        "retention_metrics",
        params={
            "exp_users_table": exp_users_table,
            "client_sql": _clickhouse_string_literal(client),
            "segment_sql": _clickhouse_string_literal(segment_name),
            "segment_hash_sql": _clickhouse_string_literal(segment_hash),
            "app_retention_unified_id_sql": "`app_unified_id`" if is_web_client else "`unified_id`",
            "calculate_web_retention_sql": "1" if calculate_web_retention else "0",
            "calculate_app_retention_sql": "1" if calculate_app_retention else "0",
            "is_web_client_sql": "1" if is_web_client else "0",
            "web_event_platform_sql": web_event_platform_sql,
            "app_product_sample_sql": app_product_sample_sql,
            "app_product_sample_multiplier_sql": app_product_sample_multiplier_sql,
        },
        config=config,
    )
    logger.info("retention query:\n%s", query)
    return execute_sql(query)


def get_tab_view_metrics(
    exp_info: dict,
    exp_users_table: str,
    client: str,
    segment_name: str,
    segment_hash: str = "",
    *,
    calculate_app_tab_view: bool = True,
    segment: Optional[dict] = None,
    clients_options: object = "",
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    is_web_client = base_client_for_calculation(client) == UG_WEB_CLIENT
    calculate_web_tab_view = is_web_client and not is_mobweb_segment(segment or {}, clients_options, client)
    web_event_platform_sql = web_event_platform_filter_sql(client, segment or {}, clients_options)
    app_product_sample_sql, app_product_sample_multiplier_sql = app_product_sample_params(
        client,
        segment or {},
        clients_options,
        config=config,
    )
    query = get_query(
        "tab_view_metrics",
        params={
            "exp_id": exp_info["id"],
            "exp_users_table": exp_users_table,
            "client_sql": _clickhouse_string_literal(client),
            "segment_sql": _clickhouse_string_literal(segment_name),
            "segment_hash_sql": _clickhouse_string_literal(segment_hash),
            "app_tab_view_unified_id_sql": "`app_unified_id`" if is_web_client else "`unified_id`",
            "calculate_web_tab_view_sql": "1" if calculate_web_tab_view else "0",
            "calculate_app_tab_view_sql": "1" if calculate_app_tab_view else "0",
            "is_web_client_sql": "1" if is_web_client else "0",
            "web_event_platform_sql": web_event_platform_sql,
            "app_product_sample_sql": app_product_sample_sql,
            "app_product_sample_multiplier_sql": app_product_sample_multiplier_sql,
        }
        | _experiment_time_params(exp_info),
        config=config,
    )
    logger.info("tab view query:\n%s", query)
    return execute_sql(query)


def get_tour_subscription_funnels(
    exp_users_table: str,
    subscription_table: str,
    client: str,
    segment_name: str,
    segment_hash: str = "",
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    return get_funnel_metrics(
        "tour_subscription_funnels",
        exp_users_table,
        subscription_table,
        client,
        segment_name,
        segment_hash,
        config=config,
    )


def get_funnel_metrics(
    query_name: str,
    exp_users_table: str,
    subscription_table: str,
    client: str,
    segment_name: str,
    segment_hash: str = "",
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    query = get_query(
        query_name,
        params={
            "exp_users_table": exp_users_table,
            "subscription_table": subscription_table,
            "client_sql": _clickhouse_string_literal(client),
            "segment_sql": _clickhouse_string_literal(segment_name),
            "segment_hash_sql": _clickhouse_string_literal(segment_hash),
        },
        config=config,
    )
    logger.info("%s query:\n%s", query_name, query)
    return execute_sql(query)


def create_results_table(table_name: str, df: pd.DataFrame, *, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    cfg = get_config(config)
    df = with_output_updated_at(df)
    schema = pandas_to_clickhouse_types(df)
    query = create_table_sql(
        table_name,
        schema=f"({schema})",
        partition="toYYYYMM(toDate(dt)), exp_id, client, segment",
        sorting="dt",
        config=cfg,
    )
    logger.info("Creating experiment results table with query:\n%s", query)
    execute_sql_modify(query)
    insert_df_by_chunks(cfg.full_table(table_name), df)


def create_exp_results_table(df: pd.DataFrame, *, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    create_results_table("ug_exp_results", df, config=config)


def create_exp_stats_table(df: pd.DataFrame, *, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    create_results_table("ug_exp_stats", df, config=config)


def create_exp_funnel_stats_table(df: pd.DataFrame, *, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    create_results_table("ug_exp_funnel_stats", df, config=config)


def create_exp_funnel_results_table(df: pd.DataFrame, *, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    create_results_table("ug_exp_funnel_results", df, config=config)


def ensure_table_columns(
    table_name: str,
    columns: dict[str, str],
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> None:
    cfg = get_config(config)
    full_table_name = cfg.full_table(table_name)
    is_exists = execute_sql(f"exists {full_table_name}")
    if int(is_exists.iloc[0].values[0]) == 0:
        return

    for column_name, column_type in columns.items():
        if _table_has_column(full_table_name, column_name):
            continue

        default_value = _default_value_for_clickhouse_type(column_type)
        query = f"""
            alter table {full_table_name}
            on cluster {cfg.cluster}
            add column if not exists `{column_name}` {column_type} default {default_value}
        """
        execute_sql_modify(query)


def _default_value_for_clickhouse_type(column_type: str) -> str:
    normalized_type = column_type.strip().lower()
    if "string" in normalized_type:
        return "''"
    if normalized_type.startswith("datetime"):
        return "toDateTime(0)"
    if normalized_type.startswith("date"):
        return "toDate(0)"
    return "0"


def update_exp_results_table(df: pd.DataFrame, table: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    cfg = get_config(config)
    ensure_table_columns(table, EXPERIMENT_OUTPUT_UPDATED_AT_COLUMNS, config=cfg)
    df = with_output_updated_at(df)
    insert_df_by_chunks(cfg.full_table(table), df)


def clear_exp_temp_tables(*, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    cfg = get_config(config)
    query = get_query(
        "get_sloperator_temp_tables",
        params={
            "database": cfg.database,
            "table_prefix": cfg.table_prefix,
        },
        config=cfg,
    )
    df = execute_sql(query)
    tables = df["table_name"].tolist()
    for table in tables:
        drop_table(f"{cfg.database}.{table}", config=cfg)
