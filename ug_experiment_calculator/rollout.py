from __future__ import annotations

import argparse
import datetime
import logging
from typing import Iterable, Optional

from clickhouse_worker import clickhouse_string_literal as _clickhouse_string_literal
from clickhouse_worker import execute_sql, execute_sql_modify
import pandas as pd

from .config import ExperimentCalculatorConfig
from .repository import (
    _collect_platform_values,
    _flatten_option_values,
    _parse_clients_options,
    create_experiment_users_table,
    create_table_sql,
    get_experiment,
    get_query,
    get_segment_hash,
    is_mobweb_segment,
)


logger = logging.getLogger(__name__)


ROLLOUT_SPLIT_USERS_TABLE = "ug_exp_rollout_split_users"
DEFAULT_IMPACT_LOOKBACK_DAYS = 14


def calculate_rollout_share(
    exp_id: int,
    *,
    clients: Optional[Iterable[str]] = None,
    segment_name: str = "Total",
    update_split_users: bool = True,
    ensure_experiment_users: bool = True,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    """Return cumulative experiment-user share among users split into the experiment.

    The split-users source table is stored daily by exp_id/client. The returned
    frame keeps the same client breakdown and has one row per client and
    calendar day in the experiment interval.
    """
    cfg = config or ExperimentCalculatorConfig.from_env()
    exp_info = get_experiment(exp_id, config=cfg)
    selected_clients = list(clients or exp_info.get("clients_list") or cfg.default_clients)
    segment = _segment_by_name(exp_info, segment_name)

    exp_start_dt, exp_end_dt = _experiment_interval(exp_info)

    exp_users_table = ""
    if ensure_experiment_users:
        for client in selected_clients:
            logger.info("Ensuring experiment users for exp_id=%s, client=%s, segment=%s", exp_id, client, segment_name)
            exp_users_table = create_experiment_users_table(exp_info, client, segment_name, segment, config=cfg)
    else:
        exp_users_table = cfg.full_table(f"exp_users_{exp_id}")

    if update_split_users:
        update_rollout_split_users_daily(
            exp_info,
            selected_clients,
            config=cfg,
        )

    experiment_daily = get_experiment_users_daily(
        exp_id,
        exp_users_table,
        selected_clients,
        segment_name,
        segment,
        config=cfg,
    )
    split_daily = get_rollout_split_users_daily(exp_id, selected_clients, config=cfg)

    return build_rollout_share_frame(
        experiment_daily,
        split_daily,
        date_start=exp_start_dt.date(),
        date_end=exp_end_dt.date(),
    )


def calculate_rollout_impact_estimate(
    exp_id: int,
    *,
    clients: Optional[Iterable[str]] = None,
    segment_name: str = "Total",
    lookback_days: int = DEFAULT_IMPACT_LOOKBACK_DAYS,
    date_end: Optional[datetime.date] = None,
    update_split_users: bool = False,
    ensure_experiment_users: bool = False,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    cfg = config or ExperimentCalculatorConfig.from_env()
    exp_info = get_experiment(exp_id, config=cfg)
    selected_clients = list(clients or exp_info.get("clients_list") or cfg.default_clients)
    period_end = date_end or (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1))
    period_start = period_end - datetime.timedelta(days=lookback_days - 1)

    rollout_share = calculate_rollout_share(
        exp_id,
        clients=selected_clients,
        segment_name=segment_name,
        update_split_users=update_split_users,
        ensure_experiment_users=ensure_experiment_users,
        config=cfg,
    )
    latest_share = _latest_rollout_share_by_client(rollout_share)
    recent_users = get_recent_client_users_daily(
        exp_info,
        selected_clients,
        date_start=period_start,
        date_end=period_end,
        config=cfg,
    )

    average_users = _recent_users_average_frame(recent_users, lookback_days=lookback_days)
    result = pd.DataFrame({"client": selected_clients}).merge(
        average_users[["client", "average_daily_users"]],
        on="client",
        how="left",
    )
    result = result.merge(latest_share, on="client", how="left")
    result["average_daily_users"] = result["average_daily_users"].fillna(0)
    result["experiment_share"] = pd.to_numeric(result["experiment_share"], errors="coerce")
    result["expected_affected_users"] = result["average_daily_users"] * result["experiment_share"]
    return result[
        [
            "client",
            "average_daily_users",
            "experiment_share",
            "expected_affected_users",
        ]
    ]


def get_recent_client_users_daily(
    exp_info: dict,
    clients: Iterable[str],
    *,
    date_start: datetime.date,
    date_end: datetime.date,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    cfg = config or ExperimentCalculatorConfig.from_env()
    frames = []

    for client in clients:
        query = _recent_client_users_daily_query(
            exp_info,
            client,
            date_start=date_start,
            date_end=date_end,
            config=cfg,
        )
        logger.info("Loading recent users for client=%s, period=%s - %s", client, date_start, date_end)
        frames.append(execute_sql(query))

    if not frames:
        return pd.DataFrame(columns=["client", "users_avg"])

    return pd.concat(frames, ignore_index=True)


def update_rollout_split_users_daily(
    exp_info: dict,
    clients: Iterable[str],
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    cfg = config or ExperimentCalculatorConfig.from_env()
    table_name = _ensure_rollout_split_users_table(config=cfg)
    exp_id = int(exp_info["id"])
    exp_start_dt, exp_end_dt = _experiment_interval(exp_info)
    selected_clients = list(clients)

    if not selected_clients:
        return table_name

    split_users_table = _ensure_rollout_split_users_raw_table(exp_info, config=cfg)
    days_cnt = (exp_end_dt.date() - exp_start_dt.date()).days

    for client in selected_clients:
        for day in range(days_cnt + 1):
            current_day = exp_start_dt + datetime.timedelta(days=day)
            if not _should_insert_rollout_split_users_day(split_users_table, current_day, client):
                logger.info(
                    "Skipping rollout split users insert for exp_id=%s, client=%s, date=%s",
                    exp_id,
                    client,
                    current_day.strftime("%Y-%m-%d"),
                )
                continue

            query = _rollout_split_users_daily_query(
                exp_info,
                client,
                current_day=current_day,
                split_users_table=split_users_table,
                config=cfg,
            )
            insert_query = f"insert into {split_users_table} ({_rollout_split_users_raw_columns_sql()})\n{query}"
            logger.info(
                "Inserting rollout split users for exp_id=%s, client=%s, date=%s",
                exp_id,
                client,
                current_day.strftime("%Y-%m-%d"),
            )
            execute_sql_modify(insert_query)

    _refresh_rollout_split_users_daily(
        exp_id,
        split_users_table,
        selected_clients,
        exp_start_dt.date(),
        exp_end_dt.date(),
        config=cfg,
    )

    return table_name


def get_rollout_split_users_daily(
    exp_id: int,
    clients: Iterable[str],
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    cfg = config or ExperimentCalculatorConfig.from_env()
    clients_sql = _clients_in_sql(clients)
    if not clients_sql:
        return pd.DataFrame(columns=["dt", "client", "split_users"])

    query = f"""
        select
            `dt`,
            `client`,
            sum(`split_users`) as `split_users`
        from {cfg.full_table(ROLLOUT_SPLIT_USERS_TABLE)}
        where
            `exp_id` = {int(exp_id)}
        and
            `client` in ({clients_sql})
        group by
            `dt`,
            `client`
        order by
            `client`,
            `dt`
    """
    return execute_sql(query)


def get_experiment_users_daily(
    exp_id: int,
    exp_users_table: str,
    clients: Iterable[str],
    segment_name: str,
    segment: dict,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    clients_sql = _clients_in_sql(clients)
    if not clients_sql:
        return pd.DataFrame(columns=["dt", "client", "experiment_users"])

    query = f"""
        select
            `dt`,
            `client`,
            count() as `experiment_users`
        from (
            select
                `client`,
                `unified_id`,
                toDate(min(`exp_start_dt`), 'UTC') as `dt`
            from {exp_users_table}
            where
                `client` in ({clients_sql})
            and
                `segment` = {_clickhouse_string_literal(segment_name)}
            and
                `segment_hash` = {_clickhouse_string_literal(get_segment_hash(segment))}
            group by
                `client`,
                `unified_id`
        )
        group by
            `dt`,
            `client`
        order by
            `client`,
            `dt`
    """
    return execute_sql(query)


def build_rollout_share_frame(
    experiment_daily: pd.DataFrame,
    split_daily: pd.DataFrame,
    *,
    date_start: datetime.date,
    date_end: datetime.date,
) -> pd.DataFrame:
    dates = pd.date_range(date_start, date_end, freq="D")
    clients = _daily_frame_clients(experiment_daily).union(_daily_frame_clients(split_daily))
    if not clients:
        return pd.DataFrame(
            columns=[
                "dt",
                "client",
                "cumulative_experiment_users",
                "cumulative_split_users",
                "experiment_share",
            ]
        )

    full_index = pd.MultiIndex.from_product([dates, sorted(clients)], names=["dt", "client"]).to_frame(index=False)
    result = full_index.merge(_normal_daily_frame(experiment_daily, "experiment_users"), on=["dt", "client"], how="left")
    result = result.merge(_normal_daily_frame(split_daily, "split_users"), on=["dt", "client"], how="left")
    result[["experiment_users", "split_users"]] = result[["experiment_users", "split_users"]].fillna(0).astype("int64")

    result = result.sort_values(["client", "dt"]).reset_index(drop=True)
    result["cumulative_experiment_users"] = result.groupby("client")["experiment_users"].cumsum()
    result["cumulative_split_users"] = result.groupby("client")["split_users"].cumsum()
    result["experiment_share"] = (
        result["cumulative_experiment_users"] / result["cumulative_split_users"].replace({0: pd.NA})
    ).astype("Float64")

    return result[
        [
            "dt",
            "client",
            "cumulative_experiment_users",
            "cumulative_split_users",
            "experiment_share",
        ]
    ]


def _ensure_rollout_split_users_table(*, config: ExperimentCalculatorConfig) -> str:
    full_table_name = config.full_table(ROLLOUT_SPLIT_USERS_TABLE)
    is_exists = execute_sql(f"exists {full_table_name}")
    if int(is_exists.iloc[0].values[0]) == 1:
        return full_table_name

    query = create_table_sql(
        ROLLOUT_SPLIT_USERS_TABLE,
        schema="""(
            `dt` Date,
            `exp_id` UInt32,
            `client` String,
            `split_users` UInt64
        )""",
        partition="toYYYYMM(`dt`), `exp_id`, `client`",
        sorting="`exp_id`, `client`, `dt`",
        config=config,
    )
    logger.info("Creating rollout split users table with query:\n%s", query)
    execute_sql_modify(query)
    return full_table_name


def _ensure_rollout_split_users_raw_table(
    exp_info: dict,
    *,
    config: ExperimentCalculatorConfig,
) -> str:
    table_name = _rollout_split_users_raw_table_name(int(exp_info["id"]))
    full_table_name = config.full_table(table_name)
    is_exists = execute_sql(f"exists {full_table_name}")
    if int(is_exists.iloc[0].values[0]) == 1:
        return full_table_name

    query = create_table_sql(
        table_name,
        schema="""(
            `exp_id` UInt32,
            `client` String,
            `unified_id` UInt64,
            `variation` UInt16,
            `first_split_dt` DateTime
        )""",
        partition="toYYYYMM(toDate(`first_split_dt`)), `client`",
        sorting="`client`, `first_split_dt`, `unified_id`",
        config=config,
    )
    logger.info("Creating rollout split users raw table with query:\n%s", query)
    execute_sql_modify(query)
    return full_table_name


def _rollout_split_users_raw_table_name(exp_id: int) -> str:
    return f"rollout_split_users_{int(exp_id)}"


def _rollout_split_users_raw_columns_sql() -> str:
    return ", ".join(f"`{column}`" for column in ("exp_id", "client", "unified_id", "variation", "first_split_dt"))


def _should_insert_rollout_split_users_day(
    table_name: str,
    current_day: datetime.datetime,
    client: str,
) -> bool:
    current_day_str = current_day.strftime("%Y-%m-%d")
    query = f"""
        select
            countIf(toDate(`first_split_dt`, 'UTC') = toDate('{current_day_str}')) as `rows_for_day`,
            max(toDate(`first_split_dt`, 'UTC')) as `max_dt`
        from {table_name}
        where
            `client` = {_clickhouse_string_literal(client)}
    """
    df = execute_sql(query)
    rows_for_day = int(df["rows_for_day"].iloc[0] or 0)
    max_dt = df["max_dt"].iloc[0]

    if rows_for_day == 0:
        return True
    if pd.isna(max_dt):
        return True

    return str(max_dt)[:10] == current_day_str


def _delete_rollout_split_users(
    exp_id: int,
    clients: Iterable[str],
    date_start: datetime.date,
    date_end: datetime.date,
    *,
    config: ExperimentCalculatorConfig,
) -> None:
    clients_sql = _clients_in_sql(clients)
    if not clients_sql:
        return

    query = f"""
        alter table {config.full_table(ROLLOUT_SPLIT_USERS_TABLE)}
        on cluster {config.cluster}
        delete where
            `exp_id` = {int(exp_id)}
        and
            `client` in ({clients_sql})
        and
            `dt` between toDate('{date_start:%Y-%m-%d}') and toDate('{date_end:%Y-%m-%d}')
        settings mutations_sync = 1
    """
    execute_sql_modify(query)


def _refresh_rollout_split_users_daily(
    exp_id: int,
    split_users_table: str,
    clients: Iterable[str],
    date_start: datetime.date,
    date_end: datetime.date,
    *,
    config: ExperimentCalculatorConfig,
) -> None:
    selected_clients = list(clients)
    clients_sql = _clients_in_sql(selected_clients)
    if not clients_sql:
        return

    _delete_rollout_split_users(exp_id, selected_clients, date_start, date_end, config=config)

    query = f"""
        insert into {config.full_table(ROLLOUT_SPLIT_USERS_TABLE)}
        select
            toDate(`first_split_dt`, 'UTC') as `dt`,
            toUInt32({int(exp_id)}) as `exp_id`,
            `client`,
            count() as `split_users`
        from {split_users_table}
        where
            `client` in ({clients_sql})
        and
            `exp_id` = {int(exp_id)}
        and
            toDate(`first_split_dt`, 'UTC') between toDate('{date_start:%Y-%m-%d}') and toDate('{date_end:%Y-%m-%d}')
        group by
            `dt`,
            `client`
        order by
            `client`,
            `dt`
    """
    logger.info(
        "Refreshing rollout split users daily aggregate for exp_id=%s, clients=%s, period=%s - %s",
        exp_id,
        selected_clients,
        date_start,
        date_end,
    )
    execute_sql_modify(query)


def _rollout_split_users_daily_query(
    exp_info: dict,
    client: str,
    *,
    current_day: datetime.datetime,
    split_users_table: str,
    config: ExperimentCalculatorConfig,
) -> str:
    exp_start_dt, exp_end_dt = _experiment_interval(exp_info)
    segment = _segment_by_name(exp_info, "Total")
    events_table, alias, platform_filter = _split_events_source(
        client,
        segment,
        clients_options=exp_info.get("clients_options", ""),
    )

    return get_query(
        "rollout_split_users_daily",
        params={
            "exp_id": int(exp_info["id"]),
            "date_filter": current_day.strftime("%Y-%m-%d"),
            "date_start_ts": int(exp_start_dt.timestamp()),
            "date_end_ts": int(exp_end_dt.timestamp()),
            "client_sql": _clickhouse_string_literal(client),
            "events_table": events_table,
            "alias": alias,
            "platform_filter": platform_filter,
            "split_users_table": split_users_table,
        },
        config=config,
    )


def _split_events_source(client: str, segment: dict, *, clients_options: object = "") -> tuple[str, str, str]:
    if client == "UG_WEB":
        platform_filter = "and `urew`.`platform` > 1" if is_mobweb_segment(segment, clients_options, client) else "and `urew`.`platform` = 1"
        return "`default`.`ug_rt_events_web`", "urew", platform_filter

    return "`default`.`ug_rt_events_app`", "urea", ""


def _recent_client_users_daily_query(
    exp_info: dict,
    client: str,
    *,
    date_start: datetime.date,
    date_end: datetime.date,
    config: ExperimentCalculatorConfig,
) -> str:
    client_options = _client_options(exp_info.get("clients_options", ""), client)
    events_table, alias, platform_filter = _recent_events_source(client, client_options)
    country_filter = _country_filter_sql(alias, _collect_country_values(client_options))
    return get_query(
        "rollout_recent_users_daily",
        params={
            "date_start": date_start.strftime("%Y-%m-%d"),
            "date_end": date_end.strftime("%Y-%m-%d"),
            "client_sql": _clickhouse_string_literal(client),
            "events_table": events_table,
            "alias": alias,
            "platform_filter": platform_filter,
            "country_filter": country_filter,
        },
        config=config,
    )


def _recent_events_source(client: str, client_options: object) -> tuple[str, str, str]:
    if client == "UG_WEB":
        platform_values = _collect_platform_values(client_options)
        platform_filter = _web_platform_filter_sql("urew", platform_values)
        return "`default`.`ug_rt_events_web`", "urew", platform_filter

    return "`default`.`ug_rt_events_app`", "urea", ""


def _web_platform_filter_sql(alias: str, platform_values: list[object]) -> str:
    if not platform_values:
        return f"and `{alias}`.`platform` > 1"

    platform_ids = set()
    has_mobile_bucket = False
    for value in platform_values:
        text = str(value).strip().lower()
        if text in {"mobile", "mobweb", "mobile_web", "mobile web", "mweb"}:
            has_mobile_bucket = True
            continue
        if text in {"desktop", "web"}:
            platform_ids.add(1)
            continue
        if text == "phone":
            platform_ids.add(2)
            continue
        if text == "tablet":
            platform_ids.add(3)
            continue
        try:
            platform_ids.add(int(text))
        except ValueError:
            continue

    if has_mobile_bucket and not platform_ids:
        return f"and `{alias}`.`platform` > 1"
    if not platform_ids:
        return f"and `{alias}`.`platform` > 1"

    values_sql = ", ".join(str(value) for value in sorted(platform_ids))
    return f"and `{alias}`.`platform` in ({values_sql})"


def _client_options(clients_options: object, client: str) -> object:
    parsed_options = _parse_clients_options(clients_options)
    if isinstance(parsed_options, dict):
        return parsed_options.get(client, {})
    return {}


def _collect_country_values(options: object) -> list[object]:
    if isinstance(options, dict):
        values = []
        for key, value in options.items():
            if str(key).lower() in {"country", "countries"}:
                values.extend(_flatten_option_values(value))
            else:
                values.extend(_collect_country_values(value))
        return values

    if isinstance(options, (list, tuple, set)):
        items = list(options)
        if len(items) == 2 and str(items[0]).lower() in {"country", "countries"}:
            return _flatten_option_values(items[1])
        values = []
        for value in options:
            values.extend(_collect_country_values(value))
        return values

    return []


def _country_filter_sql(alias: str, country_values: list[object]) -> str:
    countries = sorted({str(value).strip().upper() for value in country_values if str(value).strip()})
    if not countries or "ALL" in countries:
        return ""
    values_sql = ", ".join(_clickhouse_string_literal(country) for country in countries)
    return f"and upper(`{alias}`.`country`) in ({values_sql})"


def _segment_by_name(exp_info: dict, segment_name: str) -> dict:
    segments = exp_info.get("segments") or {}
    if segment_name not in segments:
        raise ValueError(f"Experiment {exp_info.get('id')} has no segment {segment_name!r}")
    return segments[segment_name]


def _experiment_interval(exp_info: dict) -> tuple[datetime.datetime, datetime.datetime]:
    exp_start_dt = datetime.datetime.fromtimestamp(int(exp_info["date_start"]), datetime.timezone.utc)
    exp_end_ts = int(exp_info["date_end"])
    if exp_end_ts <= int(exp_info["date_start"]):
        return exp_start_dt, datetime.datetime.now(datetime.timezone.utc)
    return exp_start_dt, datetime.datetime.fromtimestamp(exp_end_ts, datetime.timezone.utc)


def _clients_in_sql(clients: Iterable[str]) -> str:
    return ", ".join(_clickhouse_string_literal(client) for client in clients)


def _normal_daily_frame(df: pd.DataFrame, value_column: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            {
                "dt": pd.Series(dtype="datetime64[ns]"),
                "client": pd.Series(dtype="str"),
                value_column: pd.Series(dtype="int64"),
            }
        )

    result = df[["dt", "client", value_column]].copy()
    result["dt"] = pd.to_datetime(result["dt"])
    result["client"] = result["client"].fillna("").astype(str)
    result[value_column] = pd.to_numeric(result[value_column], errors="coerce").fillna(0).astype("int64")
    return result


def _daily_frame_clients(df: pd.DataFrame) -> set[str]:
    if df.empty or "client" not in df.columns:
        return set()
    return set(df["client"].dropna().astype(str))


def _latest_rollout_share_by_client(rollout_share: pd.DataFrame) -> pd.DataFrame:
    if rollout_share.empty:
        return pd.DataFrame(columns=["client", "experiment_share"])

    frame = rollout_share[["client", "dt", "experiment_share"]].copy()
    frame["dt"] = pd.to_datetime(frame["dt"])
    frame = frame.sort_values(["client", "dt"])
    latest = frame.groupby("client", as_index=False).tail(1)
    return latest[["client", "experiment_share"]].reset_index(drop=True)


def _recent_users_average_frame(recent_users: pd.DataFrame, *, lookback_days: int) -> pd.DataFrame:
    if recent_users.empty:
        return pd.DataFrame(columns=["client", "average_daily_users"])

    if "users_avg" in recent_users.columns:
        result = recent_users[["client", "users_avg"]].copy()
        result["average_daily_users"] = pd.to_numeric(result["users_avg"], errors="coerce").fillna(0)
        return result.groupby("client", as_index=False)["average_daily_users"].sum()

    result = recent_users.groupby("client", as_index=False)["users"].sum()
    result["average_daily_users"] = pd.to_numeric(result["users"], errors="coerce").fillna(0) / lookback_days
    return result[["client", "average_daily_users"]]


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate experiment rollout share by split users.")
    parser.add_argument("exp_id", type=int)
    parser.add_argument("--client", action="append", dest="clients", help="Client/source to include. Can be passed more than once.")
    parser.add_argument("--segment", default="Total", help="Experiment-users segment to use as numerator.")
    parser.add_argument("--no-update-split-users", action="store_true", help="Read existing split-users table without refreshing it.")
    parser.add_argument("--no-ensure-users", action="store_true", help="Do not create/update exp_users before calculating the share.")
    parser.add_argument("--impact", action="store_true", help="Return rollout impact estimate instead of rollout share by day.")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_IMPACT_LOOKBACK_DAYS)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.impact:
        df = calculate_rollout_impact_estimate(
            args.exp_id,
            clients=args.clients,
            segment_name=args.segment,
            update_split_users=not args.no_update_split_users,
            ensure_experiment_users=not args.no_ensure_users,
            lookback_days=args.lookback_days,
        )
    else:
        df = calculate_rollout_share(
            args.exp_id,
            clients=args.clients,
            segment_name=args.segment,
            update_split_users=not args.no_update_split_users,
            ensure_experiment_users=not args.no_ensure_users,
        )
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
