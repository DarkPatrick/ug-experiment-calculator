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
SUBSCRIPTION_SOURCE_VERSION = 5


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
    ]

    int_columns = [
        "control_variation",
        "test_variation",
        "exp_id",
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
        "subscriber_cnt",
        "otp_owner_cnt",
        "access_owner_cnt",
        "access_instant_cnt",
        "access_ex_trial_cnt",
        "access_trial_cnt",
        "trial_subscriber_cnt",
        "active_trial_cnt",
        "access_otp_cnt",
        "subscriptions_cnt",
        "access_cnt",
        "charged_trial_cnt",
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
        "active_trial_revenue",
        "lifetime_revenue",
        "arpu_var",
        "lifetime_arpu_var",
        "arppu_var",
        "subscriptions_per_user_var",
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
    platform = str(segment.get("platform", "")).lower()
    if platform in {"mobweb", "mobile_web", "mobile web", "mweb"}:
        return True
    if segment.get("mobweb") is True or segment.get("mobile_web") is True:
        return True

    segment_sql = json.dumps(segment, sort_keys=True, ensure_ascii=True, default=str).lower()
    if "platform > 1" in segment_sql or "platform>1" in segment_sql:
        return True

    parsed_options = _parse_clients_options(clients_options)
    if isinstance(parsed_options, dict):
        parsed_options = parsed_options.get(client, {})
    if _text_has_mobweb_marker(parsed_options):
        return True
    return _platform_values_are_mobweb(_collect_platform_values(parsed_options))


def exp_raw_data_query_name(client: str, segment: dict, *, clients_options: object = "", insert: bool = False) -> str:
    suffix = "_insert" if insert else ""
    if client == "UG_WEB":
        if is_mobweb_segment(segment, clients_options, client):
            return f"exp_raw_data_mobweb{suffix}"
        return f"exp_raw_data_web{suffix}"
    return f"exp_raw_data_app{suffix}"


def get_segment_hash(segment: dict) -> str:
    segment_json = json.dumps(segment, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str)
    return hashlib.sha256(segment_json.encode("utf-8")).hexdigest()


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
)


def _exp_users_insert_columns_sql() -> str:
    return ", ".join(f"`{column}`" for column in EXP_USERS_COLUMNS)


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
            `subscription_unified_ids`
        from (
            {query}
        )
    """


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


def _ensure_segment_hash_column(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    cfg = get_config(config)
    if _table_has_column(table_name, "segment_hash"):
        return

    query = f"""
        alter table {table_name}
        on cluster {cfg.cluster}
        add column if not exists `segment_hash` String default ''
    """
    execute_sql_modify(query)


def _ensure_exp_users_extra_columns(table_name: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    cfg = get_config(config)
    columns = {
        "app_unified_id": "Int64 default 0",
        "has_app": "UInt8 default 0",
        "subscription_unified_ids": "Array(Int64) default []",
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


def _ensure_exp_users_segment_hash(
    table_name: str,
    client: str,
    segment_name: str,
    segment_hash: str,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> None:
    _ensure_segment_hash_column(table_name, config=config)
    _ensure_exp_users_extra_columns(table_name, config=config)
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
    exp_id = exp_info["id"]
    exp_start_dt = datetime.datetime.fromtimestamp(exp_info["date_start"], datetime.timezone.utc)
    table_name = f"exp_users_{exp_id}"
    full_table_name = cfg.full_table(table_name)
    segment_hash = get_segment_hash(segment)

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
        seed_query_name = exp_raw_data_query_name(client, segment, clients_options=exp_info.get("clients_options", ""))
        seed_query = get_query(
            seed_query_name,
            params={
                "exp_id": exp_id,
                "where_sql": where_filter,
                "having_sql": having_filter,
                "date_filter": exp_start_dt.strftime("%Y-%m-%d"),
                "client": client,
            },
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

        query_part_1 = f"insert into {full_table_name} ({_exp_users_insert_columns_sql()})"
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
                "client": client,
                "client_sql": _clickhouse_string_literal(client),
                "segment_sql": _clickhouse_string_literal(segment_name),
                "segment_hash_sql": _clickhouse_string_literal(segment_hash),
            },
            config=cfg,
        )
        query = query_part_1 + "\n" + _wrap_exp_users_query(query_part_2, client, segment_name, segment_hash)
        logger.info("Inserting experiment users table with query:\n%s", query)
        execute_sql_modify(query)

    return full_table_name


def create_experiments_subscription_table(
    exp_info: dict,
    client: str,
    segment: dict,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    cfg = get_config(config)
    session_id = generate_random_id(32)
    table_name = f"exp_subscription_{exp_info['id']}_{session_id}"
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
    query = get_query(
        "monetization_metrics",
        params={
            "exp_users_table": exp_users_table,
            "subscription_table": subscription_table,
            "client_sql": _clickhouse_string_literal(client),
            "segment_sql": _clickhouse_string_literal(segment_name),
            "segment_hash_sql": _clickhouse_string_literal(segment_hash),
        },
        config=config,
    )
    logger.info("total query:\n%s", query)
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

        query = f"""
            alter table {full_table_name}
            on cluster {cfg.cluster}
            add column if not exists `{column_name}` {column_type} default ''
        """
        execute_sql_modify(query)


def update_exp_results_table(df: pd.DataFrame, table: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> None:
    cfg = get_config(config)
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
