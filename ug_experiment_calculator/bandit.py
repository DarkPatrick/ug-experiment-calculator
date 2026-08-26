"""Bandit (aix) experiment support: slug identity, arm registry, reconciliation.

An aix multi-armed-bandit experiment lives outside the UG A/B admin: its id is a
string slug (``ug_seasons_sale_banner_iter_4``) and its groups are *arms* whose
ids arrive as the ``aix_variant_id`` event parameter on ``default.ug_rt_events_web``.
This module owns everything slug-shaped so the rest of the pipeline keeps working
on numeric ``variation`` values:

- a persistent numeric ``output_exp_id`` per slug (result tables partition by Int64);
- a persistent arm -> variation number mapping (``ug_exp_bandit_arms``), assigned
  once per arm and never renumbered, so recalculations stay comparable;
- per-arm facts: first/last participate datetime (the arm's exposure window),
  first-touch participants and arm-switcher counts;
- a one-time reconciliation of our per-arm OEC rate against the aix counters
  captured by the lifecycle poller, with the residual divergence recorded in
  ``ug_exp_bandit_reconciliation`` instead of being re-derived per report.

Vocabulary: an aix group is always an ``arm`` (never "variation" in user-facing
labels), and the bandit cohort denominator is ``participants`` — distinct users on
``Bandit Experiment User Participate`` — never ``members`` or aix ``exposures``.
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from clickhouse_worker import (
    clickhouse_string_literal as _clickhouse_string_literal,
    execute_sql,
    execute_sql_modify,
    insert_dataframe,
)
import pandas as pd

from .config import ExperimentCalculatorConfig


logger = logging.getLogger(__name__)

BANDIT_ENTRY_EVENT = "Bandit Experiment User Participate"
BANDIT_CONTROL_ARM = "control"
# Canonical UG production host key: aix collapses every *.ultimate-guitar.com
# host onto it, while stands run under their own origins (and experiment ids).
UG_PROD_ORIGIN_HOST = "www.ultimate-guitar.com"
# Mirror of the classic to-calc semantics (status = 1 OR ended within 30 days).
DEFAULT_BANDIT_ENDED_LOOKBACK_DAYS = 30
AIX_API_TIMEOUT_SECONDS = 90
# Result-table partitions are keyed by Int64 exp_id; launch windows already use
# -(exp_id * 1000 + launch_number), so bandit ids live in their own range below it.
BANDIT_OUTPUT_EXP_ID_BASE = -1_000_000_000

BANDIT_EXPERIMENTS_TABLE = "ug_exp_bandit_experiments"
BANDIT_ARMS_TABLE = "ug_exp_bandit_arms"
BANDIT_RECONCILIATION_TABLE = "ug_exp_bandit_reconciliation"

# Written by the aix lifecycle poller (UMN-12889); shared warehouse tables without
# the calculator's table_prefix.
AIX_LIFECYCLE_ARM_SNAPSHOTS_TABLE = "ug_monetization_aix_lifecycle_arm_snapshots"
AIX_LIFECYCLE_EXPERIMENT_SNAPSHOTS_TABLE = "ug_monetization_aix_lifecycle_experiment_snapshots"
AIX_LIFECYCLE_CONCLUSIONS_TABLE = "ug_monetization_aix_lifecycle_conclusions"


def get_config(config: Optional[ExperimentCalculatorConfig] = None) -> ExperimentCalculatorConfig:
    return config or ExperimentCalculatorConfig.from_env()


def is_bandit_experiment_id(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return False
    text = str(value).strip()
    if not text:
        return False
    if text.lstrip("-").isdigit():
        return False
    return True


def _lifecycle_table(table_name: str, *, config: ExperimentCalculatorConfig) -> str:
    return f"{config.database}.{table_name}"


def _table_exists(full_table_name: str) -> bool:
    df = execute_sql(f"exists {full_table_name}")
    return int(df.iloc[0].values[0]) == 1


def _replacing_table_ddl(
    table_name: str,
    schema: str,
    *,
    version_column: str,
    sorting: str,
    config: ExperimentCalculatorConfig,
) -> str:
    return f"""
        create table if not exists {config.full_table(table_name)} on cluster {config.cluster}
        {schema}
        engine = ReplicatedReplacingMergeTree('{config.zookeeper_path(table_name)}', '{{replica}}', `{version_column}`)
        partition by tuple()
        order by ({sorting})
        settings index_granularity = 8192
    """


def _ensure_bandit_experiments_table(*, config: ExperimentCalculatorConfig) -> str:
    full_table_name = config.full_table(BANDIT_EXPERIMENTS_TABLE)
    if not _table_exists(full_table_name):
        schema = """
        (
            `aix_experiment_id` String,
            `output_exp_id` Int64,
            `entry_event` String,
            `updated_at` DateTime
        )
        """
        execute_sql_modify(
            _replacing_table_ddl(
                BANDIT_EXPERIMENTS_TABLE,
                schema,
                version_column="updated_at",
                sorting="aix_experiment_id",
                config=config,
            )
        )
    return full_table_name


def _ensure_bandit_arms_table(*, config: ExperimentCalculatorConfig) -> str:
    full_table_name = config.full_table(BANDIT_ARMS_TABLE)
    if not _table_exists(full_table_name):
        schema = """
        (
            `aix_experiment_id` String,
            `arm` String,
            `variation` UInt32,
            `first_seen_dt` DateTime,
            `last_seen_dt` DateTime,
            `participants` UInt64,
            `arm_switchers` UInt64,
            `updated_at` DateTime
        )
        """
        execute_sql_modify(
            _replacing_table_ddl(
                BANDIT_ARMS_TABLE,
                schema,
                version_column="updated_at",
                sorting="aix_experiment_id, arm",
                config=config,
            )
        )
    return full_table_name


def _ensure_bandit_reconciliation_table(*, config: ExperimentCalculatorConfig) -> str:
    full_table_name = config.full_table(BANDIT_RECONCILIATION_TABLE)
    if not _table_exists(full_table_name):
        schema = """
        (
            `aix_experiment_id` String,
            `arm` String,
            `window_start` Date,
            `window_end` Date,
            `oec_event` String,
            `wh_participants` UInt64,
            `wh_oec_users` UInt64,
            `wh_rate` Float64,
            `aix_exposures` UInt64,
            `aix_conversions` UInt64,
            `aix_rate` Float64,
            `residual_pct` Float64,
            `recorded_at` DateTime
        )
        """
        execute_sql_modify(
            _replacing_table_ddl(
                BANDIT_RECONCILIATION_TABLE,
                schema,
                version_column="recorded_at",
                sorting="aix_experiment_id, arm",
                config=config,
            )
        )
    return full_table_name


def _utc_now_naive() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


def ensure_bandit_output_exp_id(aix_experiment_id: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> int:
    cfg = get_config(config)
    full_table_name = _ensure_bandit_experiments_table(config=cfg)
    slug_literal = _clickhouse_string_literal(str(aix_experiment_id))

    existing_df = execute_sql(
        f"""
        select
            `output_exp_id`
        from {full_table_name} final
        where
            `aix_experiment_id` = {slug_literal}
        """
    )
    if not existing_df.empty:
        return int(existing_df["output_exp_id"].iloc[0])

    min_df = execute_sql(
        f"""
        select
            min(`output_exp_id`) as `min_output_exp_id`,
            count() as `rows_cnt`
        from {full_table_name} final
        """
    )
    rows_cnt = int(min_df["rows_cnt"].iloc[0] or 0)
    if rows_cnt == 0:
        output_exp_id = BANDIT_OUTPUT_EXP_ID_BASE - 1
    else:
        output_exp_id = min(int(min_df["min_output_exp_id"].iloc[0]), BANDIT_OUTPUT_EXP_ID_BASE) - 1

    row = pd.DataFrame(
        [
            {
                "aix_experiment_id": str(aix_experiment_id),
                "output_exp_id": int(output_exp_id),
                "entry_event": BANDIT_ENTRY_EVENT,
                "updated_at": _utc_now_naive(),
            }
        ]
    )
    row["output_exp_id"] = row["output_exp_id"].astype("int64")
    row["updated_at"] = pd.to_datetime(row["updated_at"]).astype("datetime64[ns]")
    insert_dataframe(full_table_name, row)
    logger.info("Registered bandit experiment %s with output_exp_id=%s", aix_experiment_id, output_exp_id)
    return int(output_exp_id)


def _get_lifecycle_experiment_status(aix_experiment_id: str, *, config: ExperimentCalculatorConfig) -> str:
    full_table_name = _lifecycle_table(AIX_LIFECYCLE_EXPERIMENT_SNAPSHOTS_TABLE, config=config)
    if not _table_exists(full_table_name):
        return ""
    df = execute_sql(
        f"""
        select
            argMax(`status`, `observed_at`) as `last_status`
        from {full_table_name}
        where
            `experiment_id` = {_clickhouse_string_literal(str(aix_experiment_id))}
        """
    )
    if df.empty:
        return ""
    return str(df["last_status"].iloc[0] or "")


def get_lifecycle_experiment_oec_event(aix_experiment_id: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> str:
    cfg = get_config(config)
    full_table_name = _lifecycle_table(AIX_LIFECYCLE_EXPERIMENT_SNAPSHOTS_TABLE, config=cfg)
    if not _table_exists(full_table_name):
        return ""
    df = execute_sql(
        f"""
        select
            argMax(`oec_event`, `observed_at`) as `last_oec_event`
        from {full_table_name}
        where
            `experiment_id` = {_clickhouse_string_literal(str(aix_experiment_id))}
        """
    )
    if df.empty:
        return ""
    return str(df["last_oec_event"].iloc[0] or "")


def get_bandit_experiment(aix_experiment_id: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> dict:
    """Build calculator exp_info for an aix bandit experiment slug.

    The window comes from the slug's own participate events; there is no admin
    registry row, no launch history and no clients configuration for a bandit.
    """
    from .repository import get_query, UG_WEB_BANDIT_CLIENT, _identifier_part

    cfg = get_config(config)
    slug = str(aix_experiment_id).strip()
    if not is_bandit_experiment_id(slug):
        raise ValueError(f"{aix_experiment_id!r} is not a bandit experiment slug")

    window_df = execute_sql(
        get_query(
            "bandit_experiment_window",
            params={
                "events_start_date": cfg.bandit_events_start_date.strftime("%Y-%m-%d"),
                "entry_event": BANDIT_ENTRY_EVENT,
                "aix_experiment_id_sql": _clickhouse_string_literal(slug),
            },
            config=cfg,
        )
    )
    users_cnt = int(window_df["users_cnt"].iloc[0] or 0) if not window_df.empty else 0
    if users_cnt == 0:
        raise ValueError(
            f"No '{BANDIT_ENTRY_EVENT}' events found for aix experiment {slug!r} "
            f"since {cfg.bandit_events_start_date}"
        )

    first_event_dt = pd.to_datetime(window_df["first_event_dt"].iloc[0])
    last_event_dt = pd.to_datetime(window_df["last_event_dt"].iloc[0])
    # Client-side clocks can stamp events in the future; never let the window
    # end run ahead of now.
    now_dt = pd.Timestamp.utcnow().tz_localize(None)
    if last_event_dt > now_dt:
        last_event_dt = now_dt
    date_start = int(first_event_dt.timestamp())
    lifecycle_status = _get_lifecycle_experiment_status(slug, config=cfg)
    today_utc = datetime.datetime.now(datetime.timezone.utc).date()
    if lifecycle_status == "active":
        date_end = 0
    elif lifecycle_status:
        date_end = int(last_event_dt.timestamp())
    else:
        # No lifecycle info: treat as running while events are still arriving.
        date_end = 0 if last_event_dt.date() >= today_utc - datetime.timedelta(days=1) else int(last_event_dt.timestamp())

    output_exp_id = ensure_bandit_output_exp_id(slug, config=cfg)
    storage_id = _identifier_part(slug)

    exp_info = {
        "id": slug,
        "is_bandit": True,
        "aix_experiment_id": slug,
        "base_id": output_exp_id,
        "output_exp_id": output_exp_id,
        "storage_id": storage_id,
        "exp_launch_id": slug,
        "launch_number": 1,
        "is_latest_launch": True,
        "date_start": date_start,
        "date_end": date_end,
        "variations": int(window_df["arms_cnt"].iloc[0] or 0),
        "experiment_event_start": BANDIT_ENTRY_EVENT,
        "configuration": "",
        "clients_list": [UG_WEB_BANDIT_CLIENT],
        "clients_options": "",
        "name": slug,
        "project": "",
        "segments": {"Total": {"pro_rights": "All"}},
        "aix_lifecycle_status": lifecycle_status,
    }
    logger.info("bandit exp_info: %s", exp_info)
    return exp_info


def get_bandit_arm_registry(aix_experiment_id: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> pd.DataFrame:
    cfg = get_config(config)
    full_table_name = cfg.full_table(BANDIT_ARMS_TABLE)
    if not _table_exists(full_table_name):
        return pd.DataFrame(columns=["arm", "variation", "first_seen_dt", "last_seen_dt", "participants", "arm_switchers"])
    return execute_sql(
        f"""
        select
            `arm`,
            `variation`,
            `first_seen_dt`,
            `last_seen_dt`,
            `participants`,
            `arm_switchers`
        from {full_table_name} final
        where
            `aix_experiment_id` = {_clickhouse_string_literal(str(aix_experiment_id))}
        order by
            `variation`
        """
    )


def get_bandit_arm_variation_map(aix_experiment_id: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> dict[str, int]:
    registry_df = get_bandit_arm_registry(aix_experiment_id, config=config)
    return {str(row.arm): int(row.variation) for row in registry_df.itertuples(index=False)}


def discover_bandit_arms(exp_info: dict, *, config: Optional[ExperimentCalculatorConfig] = None) -> pd.DataFrame:
    from .repository import get_query

    cfg = get_config(config)
    return execute_sql(
        get_query(
            "bandit_arm_discovery",
            params={
                "entry_event": BANDIT_ENTRY_EVENT,
                "aix_experiment_id_sql": _clickhouse_string_literal(str(exp_info["aix_experiment_id"])),
                "exp_start_ts": int(exp_info.get("date_start", 0) or 0),
                "exp_end_ts": int(exp_info.get("date_end", 0) or 0),
            },
            config=cfg,
        )
    )


def assign_arm_variations(
    discovered_arms: list[dict],
    existing_map: dict[str, int],
) -> dict[str, int]:
    """Assign stable variation numbers to arms.

    Existing assignments are never changed. On first assignment the ``control``
    arm gets 1 and the rest follow first-seen order; later-appearing arms are
    appended after the current maximum, so recalculations never renumber arms.
    """
    result = dict(existing_map)
    used_numbers = set(result.values())
    new_arms = [dict(arm) for arm in discovered_arms if str(arm["arm"]) not in result]
    if not new_arms:
        return result

    def sort_key(arm: dict):
        return (
            0 if str(arm["arm"]) == BANDIT_CONTROL_ARM else 1,
            arm.get("first_seen_dt"),
            str(arm["arm"]),
        )

    next_number = max(used_numbers, default=0) + 1
    for arm in sorted(new_arms, key=sort_key):
        arm_name = str(arm["arm"])
        if arm_name == BANDIT_CONTROL_ARM and 1 not in used_numbers:
            result[arm_name] = 1
            used_numbers.add(1)
            next_number = max(used_numbers) + 1
            continue
        result[arm_name] = next_number
        used_numbers.add(next_number)
        next_number += 1
    return result


def ensure_bandit_arms(exp_info: dict, *, config: Optional[ExperimentCalculatorConfig] = None) -> dict[str, int]:
    """Discover arms in the experiment window, persist per-arm facts, return arm -> variation."""
    cfg = get_config(config)
    full_table_name = _ensure_bandit_arms_table(config=cfg)
    slug = str(exp_info["aix_experiment_id"])

    discovered_df = discover_bandit_arms(exp_info, config=cfg)
    if discovered_df.empty:
        raise ValueError(f"No arms discovered for bandit experiment {slug!r}")

    existing_map = get_bandit_arm_variation_map(slug, config=cfg)
    discovered_arms = discovered_df.to_dict("records")
    mapping = assign_arm_variations(discovered_arms, existing_map)

    updated_at = _utc_now_naive()
    facts = pd.DataFrame(
        [
            {
                "aix_experiment_id": slug,
                "arm": str(arm["arm"]),
                "variation": int(mapping[str(arm["arm"])]),
                "first_seen_dt": pd.to_datetime(arm["first_seen_dt"]),
                "last_seen_dt": pd.to_datetime(arm["last_seen_dt"]),
                "participants": int(arm.get("participants_cnt", 0) or 0),
                "arm_switchers": int(arm.get("arm_switchers_cnt", 0) or 0),
                "updated_at": updated_at,
            }
            for arm in discovered_arms
        ]
    )
    facts["variation"] = facts["variation"].astype("uint32")
    facts["participants"] = facts["participants"].astype("uint64")
    facts["arm_switchers"] = facts["arm_switchers"].astype("uint64")
    for column in ("first_seen_dt", "last_seen_dt", "updated_at"):
        facts[column] = pd.to_datetime(facts[column]).astype("datetime64[ns]")
    insert_dataframe(full_table_name, facts)
    logger.info("Updated bandit arm facts for %s: %s arms", slug, len(facts))
    return mapping


def arm_variation_transform_sql(mapping: dict[str, int], arm_expr: str) -> str:
    """Build a ClickHouse expression mapping an arm slug expression to its variation number."""
    if not mapping:
        return "toUInt32(0)"
    arms = sorted(mapping)
    arm_literals = ", ".join(_clickhouse_string_literal(arm) for arm in arms)
    numbers = ", ".join(f"toUInt32({int(mapping[arm])})" for arm in arms)
    return f"transform({arm_expr}, [{arm_literals}], [{numbers}], toUInt32(0))"


def get_bandit_reconciliation(aix_experiment_id: str, *, config: Optional[ExperimentCalculatorConfig] = None) -> pd.DataFrame:
    cfg = get_config(config)
    full_table_name = cfg.full_table(BANDIT_RECONCILIATION_TABLE)
    if not _table_exists(full_table_name):
        return pd.DataFrame()
    return execute_sql(
        f"""
        select
            `arm`,
            `window_start`,
            `window_end`,
            `oec_event`,
            `wh_participants`,
            `wh_oec_users`,
            `wh_rate`,
            `aix_exposures`,
            `aix_conversions`,
            `aix_rate`,
            `residual_pct`,
            `recorded_at`
        from {full_table_name} final
        where
            `aix_experiment_id` = {_clickhouse_string_literal(str(aix_experiment_id))}
        order by
            `arm`
        """
    )


def _get_lifecycle_arm_counters(aix_experiment_id: str, *, config: ExperimentCalculatorConfig) -> pd.DataFrame:
    full_table_name = _lifecycle_table(AIX_LIFECYCLE_ARM_SNAPSHOTS_TABLE, config=config)
    if not _table_exists(full_table_name):
        return pd.DataFrame()
    return execute_sql(
        f"""
        select
            `variant_id` as `arm`,
            argMax(coalesce(`exposures`, 0), `observed_at`) as `aix_exposures`,
            argMax(coalesce(`conversions`, 0), `observed_at`) as `aix_conversions`
        from {full_table_name}
        where
            `experiment_id` = {_clickhouse_string_literal(str(aix_experiment_id))}
        group by
            `variant_id`
        """
    )


def reconcile_bandit_experiment(
    exp_info: dict,
    our_totals_df: pd.DataFrame,
    *,
    force: bool = False,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    """Record the one-time reconciliation of our per-arm OEC rate against aix counters.

    ``our_totals_df`` must carry one row per variation with ``participants`` and
    ``banner_click_user_cnt`` totals over the calculation window. The recorded
    residual is kept as-is on later runs (the gap is structural: different dedup,
    aix retention, the 1500 ms assignment wait) — pass ``force=True`` to re-record.
    """
    cfg = get_config(config)
    slug = str(exp_info["aix_experiment_id"])
    full_table_name = _ensure_bandit_reconciliation_table(config=cfg)

    if not force:
        existing_df = execute_sql(
            f"""
            select
                count() as `rows_cnt`
            from {full_table_name} final
            where
                `aix_experiment_id` = {_clickhouse_string_literal(slug)}
            """
        )
        if int(existing_df["rows_cnt"].iloc[0] or 0) > 0:
            logger.info("Bandit reconciliation for %s already recorded; skipping (use force=True to re-record)", slug)
            return get_bandit_reconciliation(slug, config=cfg)

    aix_df = _get_lifecycle_arm_counters(slug, config=cfg)
    if aix_df.empty:
        logger.warning(
            "No aix lifecycle counters found for %s (table %s missing or empty); reconciliation skipped",
            slug,
            AIX_LIFECYCLE_ARM_SNAPSHOTS_TABLE,
        )
        return pd.DataFrame()

    if our_totals_df is None or our_totals_df.empty:
        logger.warning("No warehouse totals passed for %s; reconciliation skipped", slug)
        return pd.DataFrame()

    oec_event = get_lifecycle_experiment_oec_event(slug, config=cfg) or "click"
    variation_to_arm = {int(variation): arm for arm, variation in get_bandit_arm_variation_map(slug, config=cfg).items()}

    exp_start_dt = datetime.datetime.fromtimestamp(int(exp_info["date_start"]), datetime.timezone.utc)
    date_end_ts = int(exp_info.get("date_end", 0) or 0)
    exp_end_dt = (
        datetime.datetime.fromtimestamp(date_end_ts, datetime.timezone.utc)
        if date_end_ts > int(exp_info["date_start"])
        else datetime.datetime.now(datetime.timezone.utc)
    )

    aix_by_arm = {str(row.arm): row for row in aix_df.itertuples(index=False)}
    recorded_at = _utc_now_naive()
    rows = []
    for our_row in our_totals_df.itertuples(index=False):
        arm = variation_to_arm.get(int(our_row.variation))
        if arm is None:
            continue
        wh_participants = int(getattr(our_row, "participants", 0) or 0)
        wh_oec_users = int(getattr(our_row, "banner_click_user_cnt", 0) or 0)
        wh_rate = wh_oec_users / wh_participants if wh_participants else 0.0
        aix_row = aix_by_arm.get(str(arm))
        aix_exposures = int(getattr(aix_row, "aix_exposures", 0) or 0) if aix_row is not None else 0
        aix_conversions = int(getattr(aix_row, "aix_conversions", 0) or 0) if aix_row is not None else 0
        aix_rate = aix_conversions / aix_exposures if aix_exposures else 0.0
        residual_pct = (wh_rate - aix_rate) / aix_rate * 100 if aix_rate else 0.0
        rows.append(
            {
                "aix_experiment_id": slug,
                "arm": str(arm),
                "window_start": exp_start_dt.date(),
                "window_end": exp_end_dt.date(),
                "oec_event": oec_event,
                "wh_participants": wh_participants,
                "wh_oec_users": wh_oec_users,
                "wh_rate": wh_rate,
                "aix_exposures": aix_exposures,
                "aix_conversions": aix_conversions,
                "aix_rate": aix_rate,
                "residual_pct": residual_pct,
                "recorded_at": recorded_at,
            }
        )

    if not rows:
        logger.warning("No reconciliation rows built for %s; skipped", slug)
        return pd.DataFrame()

    reconciliation_df = pd.DataFrame(rows)
    for column in ("wh_participants", "wh_oec_users", "aix_exposures", "aix_conversions"):
        reconciliation_df[column] = reconciliation_df[column].astype("uint64")
    for column in ("wh_rate", "aix_rate", "residual_pct"):
        reconciliation_df[column] = reconciliation_df[column].astype("float64")
    reconciliation_df["recorded_at"] = pd.to_datetime(reconciliation_df["recorded_at"]).astype("datetime64[ns]")
    insert_dataframe(full_table_name, reconciliation_df)
    logger.info("Recorded bandit reconciliation for %s: %s arms, oec=%s", slug, len(rows), oec_event)
    return get_bandit_reconciliation(slug, config=cfg)


def _origin_host(origin: object) -> str:
    text = str(origin or "").strip().lower()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    return urllib.parse.urlparse(text).netloc


def _fetch_aix_api_experiments() -> Optional[pd.DataFrame]:
    """Read the live experiment list from the aix instance (basic auth).

    Credentials come from the environment (AI_BANDIT_URL / AI_BANDIT_LOGIN /
    AI_BANDIT_PASSWORD, loaded from .env by ExperimentCalculatorConfig).
    Returns None when they are absent or the call fails — callers fall back to
    the lifecycle poller snapshots.
    """
    base_url = os.environ.get("AI_BANDIT_URL", "").strip()
    login = os.environ.get("AI_BANDIT_LOGIN", "").strip()
    password = os.environ.get("AI_BANDIT_PASSWORD", "")
    if not base_url or not login or not password:
        return None

    url = base_url.rstrip("/") + "/api/experiments"
    token = base64.b64encode(f"{login}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(request, timeout=AIX_API_TIMEOUT_SECONDS) as response:
            items = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("aix API experiment list failed (%s); falling back to lifecycle snapshots", exc)
        return None

    if not isinstance(items, list):
        logger.warning("aix API returned unexpected payload type %s; falling back to lifecycle snapshots", type(items))
        return None

    rows = [
        {
            "aix_experiment_id": str(item.get("experiment_id") or ""),
            "status": str(item.get("status") or ""),
            "origin": str(item.get("origin") or ""),
            "product_id": str(item.get("product_id") or ""),
            "created_at": pd.to_datetime(item.get("created_at"), errors="coerce", utc=True),
        }
        for item in items
        if isinstance(item, dict) and item.get("experiment_id")
    ]
    return pd.DataFrame(rows)


def _fetch_lifecycle_experiments(*, config: ExperimentCalculatorConfig) -> pd.DataFrame:
    full_table_name = _lifecycle_table(AIX_LIFECYCLE_EXPERIMENT_SNAPSHOTS_TABLE, config=config)
    if not _table_exists(full_table_name):
        return pd.DataFrame()
    df = execute_sql(
        f"""
        select
            `aes`.`experiment_id` as `aix_experiment_id`,
            argMax(`aes`.`status`, `aes`.`observed_at`) as `status`,
            argMax(`aes`.`origin`, `aes`.`observed_at`) as `origin`,
            argMax(`aes`.`product_id`, `aes`.`observed_at`) as `product_id`,
            argMax(`aes`.`created_at`, `aes`.`observed_at`) as `created_at`
        from {full_table_name} as `aes`
        group by
            `aix_experiment_id`
        """
    )
    if not df.empty:
        df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    return df


def _fetch_lifecycle_closed_at(*, config: ExperimentCalculatorConfig) -> dict[str, pd.Timestamp]:
    full_table_name = _lifecycle_table(AIX_LIFECYCLE_CONCLUSIONS_TABLE, config=config)
    if not _table_exists(full_table_name):
        return {}
    df = execute_sql(
        f"""
        select
            `alc`.`experiment_id` as `aix_experiment_id`,
            max(`alc`.`closed_at`) as `closed_at`
        from {full_table_name} as `alc`
        group by
            `aix_experiment_id`
        """
    )
    result = {}
    for row in df.itertuples(index=False):
        closed_at = pd.to_datetime(row.closed_at, errors="coerce", utc=True)
        if not pd.isna(closed_at):
            result[str(row.aix_experiment_id)] = closed_at
    return result


def filter_bandit_experiments(
    experiments_df: pd.DataFrame,
    *,
    origin_host: str = UG_PROD_ORIGIN_HOST,
    include_ended_days: int = DEFAULT_BANDIT_ENDED_LOOKBACK_DAYS,
    now: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """Keep experiments on the given origin host: active ones plus paused/closed
    ones that ended within the lookback (mirrors the classic to-calc semantics).

    ``closed_at`` is used when known; otherwise ``created_at`` is the proxy —
    aix experiments live for days, so a recently created one is recent, period.
    """
    if experiments_df is None or experiments_df.empty:
        return pd.DataFrame(columns=["aix_experiment_id", "status", "origin", "product_id", "created_at", "closed_at"])

    df = experiments_df.copy()
    if "closed_at" not in df.columns:
        df["closed_at"] = pd.NaT
    target_host = _origin_host(origin_host) or str(origin_host).strip().lower()
    df = df[df["origin"].map(_origin_host) == target_host]
    if df.empty:
        return df

    now_ts = now if now is not None else pd.Timestamp.now(tz="UTC")
    threshold = now_ts - pd.Timedelta(days=int(include_ended_days))

    def keep(row) -> bool:
        status = str(row["status"]).strip().lower()
        if status == "active":
            return True
        ended_at = row["closed_at"]
        if pd.isna(ended_at):
            ended_at = row["created_at"]
        if pd.isna(ended_at):
            return False
        return ended_at >= threshold

    return df[df.apply(keep, axis=1)].reset_index(drop=True)


def get_bandit_experiments(
    origin_host: str = UG_PROD_ORIGIN_HOST,
    *,
    include_ended_days: int = DEFAULT_BANDIT_ENDED_LOOKBACK_DAYS,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    """Discover bandit experiments for an origin host.

    Primary source is the live aix instance (AI_BANDIT_* credentials); when
    they are absent or the call fails, the lifecycle poller snapshots serve
    the same list from ClickHouse. ``closed_at`` always comes from the
    lifecycle conclusions when available.
    """
    cfg = get_config(config)
    experiments_df = _fetch_aix_api_experiments()
    source = "aix API"
    if experiments_df is None:
        experiments_df = _fetch_lifecycle_experiments(config=cfg)
        source = "lifecycle snapshots"
    if experiments_df.empty:
        logger.warning("No bandit experiments discovered from %s", source)
        return experiments_df

    closed_at_map = _fetch_lifecycle_closed_at(config=cfg)
    experiments_df = experiments_df.copy()
    experiments_df["closed_at"] = experiments_df["aix_experiment_id"].map(
        lambda slug: closed_at_map.get(str(slug), pd.NaT)
    )
    result = filter_bandit_experiments(
        experiments_df,
        origin_host=origin_host,
        include_ended_days=include_ended_days,
    )
    logger.info(
        "Discovered %s bandit experiments for host %s via %s: %s",
        len(result),
        origin_host,
        source,
        result["aix_experiment_id"].tolist() if not result.empty else [],
    )
    return result


def get_bandit_exps_list(
    origin_host: str = UG_PROD_ORIGIN_HOST,
    *,
    include_ended_days: int = DEFAULT_BANDIT_ENDED_LOOKBACK_DAYS,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> list[str]:
    """Slugs of bandit experiments to calculate, the bandit analog of
    ``get_exps_list``: active plus recently ended, production origin only."""
    experiments_df = get_bandit_experiments(
        origin_host,
        include_ended_days=include_ended_days,
        config=config,
    )
    if experiments_df.empty:
        return []
    return [str(slug) for slug in experiments_df["aix_experiment_id"].tolist()]
