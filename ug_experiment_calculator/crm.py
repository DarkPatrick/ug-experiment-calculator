"""CRM (Bloomreach) email experiments.

A CRM experiment is an admin row with ``provider_id = 1`` ("Imported experiment
[BLOOMREACH/<hex>]"): Bloomreach splits the recipients of one campaign and logs
'AB Test Start' into ``monetisation_crm.ug_bloomreach_campaign_events``. The
admin row has no start event, no client options and no segments, and none of the
app/web cohort queries can see those recipients, so a CRM experiment gets its own
cohort (client ``UG_CRM``), a fixed attribution window and its own segments.
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from clickhouse_worker import clickhouse_string_literal as _clickhouse_string_literal
from clickhouse_worker import execute_sql
import pandas as pd

from .config import ExperimentCalculatorConfig


logger = logging.getLogger(__name__)


CRM_PROVIDER_ID = 1
UG_CRM_CLIENT = "UG_CRM"
CRM_ENTRY_EVENT = "AB Test Start"
# The CRM process reads every send with a 3-day attribution window, for interim
# and final reads alike (Confluence "UG AI Analytics & CRM", page 852003068).
CRM_ATTRIBUTION_WINDOW_DAYS = 3
# A send is one-off: recalculate while its 14-day guardrails mature.
CRM_CALCULATION_LOOKBACK_DAYS = 30

CRM_SEGMENT_ALL = "Total"
CRM_SEGMENT_CRM = "CRM purchases"
CRM_SEGMENT_CAMPAIGN = "Campaign purchases"

# Outside CRM mode an email purchase belongs to the CRM team, not to the product
# experiment it happened to land in, so the joined subscription source drops it.
DEFAULT_SOURCE_FILTER_SQL = "lower(`use`.`funnel_source`) not like '%email%'"
DEFAULT_CRM_SUBSCRIPTION_SQL = "lower(`use`.`funnel_source`) like '%email%'"

RIGHTS_SEGMENT_KEYS = ("pro_rights", "edu_rights", "sing_rights", "practice_rights", "book_rights")

CRM_EMAIL_METRIC_COLUMNS = [
    "crm_participants",
    "email_sent_user_cnt",
    "email_delivered_user_cnt",
    "email_opened_user_cnt",
    "email_clicked_user_cnt",
    "email_unsubscribed_user_cnt",
    "email_delivered_opened_user_cnt",
    "email_opened_clicked_user_cnt",
    "email_delivered_clicked_user_cnt",
]


def get_config(config: Optional[ExperimentCalculatorConfig] = None) -> ExperimentCalculatorConfig:
    return config or ExperimentCalculatorConfig.from_env()


def is_crm_admin_row(provider_id: object) -> bool:
    try:
        return int(provider_id) == CRM_PROVIDER_ID
    except (TypeError, ValueError):
        return False


def is_crm_exp_info(exp_info: dict) -> bool:
    return bool(exp_info.get("is_crm"))


def is_crm_client(client: str) -> bool:
    return str(client) == UG_CRM_CLIENT


def attribution_window_seconds(exp_info: dict) -> int:
    """Seconds after a member's entry in which their purchases count; 7 days outside CRM."""
    return int(exp_info.get("attribution_window_seconds") or 7 * 86400)


def source_filter_sql(exp_info: dict) -> str:
    return "1" if is_crm_exp_info(exp_info) else DEFAULT_SOURCE_FILTER_SQL


def crm_subscription_sql(exp_info: dict) -> str:
    """A CRM purchase: an email funnel_source, or utm_medium = crm in the attribution.

    ``funnel_source = email_*`` covers ~92% of the utm_medium = crm purchases on
    web; the rest (a CRM click that ended on a generic paywall) is visible only in
    the attribution table, which the CRM dashboards read.
    """
    if not is_crm_exp_info(exp_info):
        return DEFAULT_CRM_SUBSCRIPTION_SQL
    date_start, date_end = _attribution_dates(exp_info)
    return (
        "(lower(`use`.`funnel_source`) like '%email%' or `use`.`subscription_id` in ("
        "select `mua`.`subscription_id` from `monetisation_paid_ua`.`marketing_ug_subs_attribution_dbt` as `mua` "
        "where `mua`.`event` = 'Subscribed' and lower(`mua`.`utm_medium`) = 'crm' "
        f"and toDate(`mua`.`datetime`) between toDate('{date_start}') and toDate('{date_end}')))"
    )


def _campaign_subscription_sql(exp_info: dict, utm_campaigns: list[str]) -> str:
    """Purchases attributed to a UTM campaign this experiment's recipients clicked."""
    if not utm_campaigns:
        return "0"
    date_start, date_end = _attribution_dates(exp_info)
    values_sql = ", ".join(_clickhouse_string_literal(value) for value in utm_campaigns)
    return (
        f"(lower(`use`.`funnel_source`) in ({values_sql}) or `use`.`subscription_id` in ("
        "select `mua`.`subscription_id` from `monetisation_paid_ua`.`marketing_ug_subs_attribution_dbt` as `mua` "
        f"where `mua`.`event` = 'Subscribed' and lower(`mua`.`utm_campaign`) in ({values_sql}) "
        f"and toDate(`mua`.`datetime`) between toDate('{date_start}') and toDate('{date_end}')))"
    )


def _attribution_dates(exp_info: dict) -> tuple[str, str]:
    start = datetime.datetime.fromtimestamp(int(exp_info["date_start"]), datetime.timezone.utc).date()
    end_ts = int(exp_info.get("date_end") or 0)
    end = (
        datetime.datetime.fromtimestamp(end_ts, datetime.timezone.utc).date()
        if end_ts > int(exp_info["date_start"])
        else datetime.datetime.now(datetime.timezone.utc).date()
    )
    end = end + datetime.timedelta(seconds=attribution_window_seconds(exp_info))
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def subscription_window_end(exp_info: dict, exp_end_dt: datetime.datetime) -> datetime.datetime:
    """Last moment a CRM member's purchase can still be attributed, capped at now."""
    if not is_crm_exp_info(exp_info):
        return exp_end_dt
    now = datetime.datetime.now(datetime.timezone.utc)
    return min(exp_end_dt + datetime.timedelta(seconds=attribution_window_seconds(exp_info)), now)


def build_crm_experiment(admin_exp_info: dict, *, config: Optional[ExperimentCalculatorConfig] = None) -> dict:
    """Turn an admin ``provider_id = 1`` row into calculator exp_info.

    The window is the campaign's own 'AB Test Start' events (first to last), not
    the admin dates: the admin row of an imported experiment is often left running
    for months after the one-off send.
    """
    from .repository import get_query

    cfg = get_config(config)
    provider_experiment_id = str(admin_exp_info.get("provider_experiment_id") or "").strip()
    if not provider_experiment_id:
        raise ValueError(f"CRM experiment {admin_exp_info.get('id')} has no provider_experiment_id")

    admin_start = int(admin_exp_info.get("date_start") or 0)
    admin_end = int(admin_exp_info.get("date_end") or 0)
    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    window_df = execute_sql(
        get_query(
            "crm_experiment_window",
            params={
                "date_start_ts": admin_start,
                "date_scan_end_ts": admin_end if admin_end > admin_start else now_ts,
                "attribution_window_days": CRM_ATTRIBUTION_WINDOW_DAYS,
                "provider_experiment_id_sql": _clickhouse_string_literal(provider_experiment_id),
            },
            config=cfg,
        )
    )
    users_cnt = int(window_df["users_cnt"].iloc[0] or 0) if not window_df.empty else 0
    if users_cnt == 0:
        raise ValueError(
            f"No '{CRM_ENTRY_EVENT}' events found for CRM experiment {admin_exp_info.get('id')} "
            f"(Bloomreach campaign {provider_experiment_id})"
        )

    first_event_dt = pd.to_datetime(window_df["first_event_dt"].iloc[0])
    last_event_dt = pd.to_datetime(window_df["last_event_dt"].iloc[0])
    send_dt = pd.to_datetime(window_df["send_dt"].iloc[0]) if "send_dt" in window_df.columns else first_event_dt
    clicked_utm_campaigns = [str(value) for value in list(window_df["clicked_utm_campaigns"].iloc[0] or [])]

    exp_info = dict(admin_exp_info)
    exp_info.update(
        {
            "is_crm": True,
            "provider_experiment_id": provider_experiment_id,
            # Timestamps of the cohort intake; the send usually takes under a minute.
            "date_start": int(first_event_dt.replace(tzinfo=datetime.timezone.utc).timestamp()),
            "date_end": int(last_event_dt.replace(tzinfo=datetime.timezone.utc).timestamp()) + 1,
            # The mass send (1st percentile of the starts): the split date of the report. date_start stays the
            # first start so that no participant falls outside the cohort window.
            "crm_send_ts": int(send_dt.replace(tzinfo=datetime.timezone.utc).timestamp()),
            "admin_date_start": admin_start,
            "admin_date_end": admin_end,
            "experiment_event_start": CRM_ENTRY_EVENT,
            "attribution_window_seconds": CRM_ATTRIBUTION_WINDOW_DAYS * 86400,
            "clients_list": [UG_CRM_CLIENT],
            "clients_options": "",
            "crm_clicked_utm_campaigns": clicked_utm_campaigns,
        }
    )
    crm_sql = crm_subscription_sql(exp_info)
    standard_segments = {
        # After Split, every purchase of the recipients (any source).
        CRM_SEGMENT_ALL: {"pro_rights": "All"},
        # After Split, pure CRM: what the CRM team reads as the split result.
        CRM_SEGMENT_CRM: {"pro_rights": "All", "swf": crm_sql},
        # After Click: purchases attributed to a campaign the recipients clicked.
        CRM_SEGMENT_CAMPAIGN: {
            "pro_rights": "All",
            "swf": f"({crm_sql}) and {_campaign_subscription_sql(exp_info, clicked_utm_campaigns)}",
        },
    }
    exp_info["segments"] = {**standard_segments, **_admin_crm_segments(admin_exp_info, standard_segments)}
    logger.info("crm exp_info: %s", exp_info)
    return exp_info


def _admin_crm_segments(admin_exp_info: dict, standard_segments: dict) -> dict:
    """Segments the experiment owner put into the admin Configuration, on top of the standard ones.

    In CRM mode `uwf` filters the raw `ug_bloomreach_campaign_events` rows and
    `swf` / `shf` the joined subscriptions, as in any experiment. Rights filters
    cannot apply: a Bloomreach event carries no rights, so they are reset to "All"
    instead of silently emptying the segment. The standard segment names win.
    """
    result = {}
    for name, segment in (admin_exp_info.get("segments") or {}).items():
        if name in standard_segments:
            continue
        segment = dict(segment or {})
        for key in RIGHTS_SEGMENT_KEYS:
            if str(segment.get(key, "all")).lower() != "all":
                logger.warning(
                    "CRM experiment %s, segment %r: %s=%r ignored (Bloomreach events carry no rights)",
                    admin_exp_info.get("id"),
                    name,
                    key,
                    segment[key],
                )
                segment[key] = "All"
        result[str(name)] = segment
    return result


def get_crm_exps_list(*, config: Optional[ExperimentCalculatorConfig] = None) -> list[int]:
    from .repository import get_query

    cfg = get_config(config)
    query = get_query(
        "get_crm_exps_ids_to_calc",
        params={"provider_id": CRM_PROVIDER_ID, "lookback_days": CRM_CALCULATION_LOOKBACK_DAYS},
        config=cfg,
    )
    return execute_sql(query)["id"].astype(int).tolist()


def get_crm_email_metrics(
    exp_info: dict,
    exp_users_table: str,
    client: str,
    segment_name: str,
    segment_hash: str,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    from .repository import get_query

    cfg = get_config(config)
    date_start = datetime.datetime.fromtimestamp(int(exp_info["date_start"]), datetime.timezone.utc)
    date_end = datetime.datetime.fromtimestamp(int(exp_info["date_end"]), datetime.timezone.utc)
    query = get_query(
        "crm_email_metrics",
        params={
            "exp_users_table": exp_users_table,
            "client_sql": _clickhouse_string_literal(client),
            "segment_sql": _clickhouse_string_literal(segment_name),
            "segment_hash_sql": _clickhouse_string_literal(segment_hash),
            "provider_experiment_id_sql": _clickhouse_string_literal(str(exp_info["provider_experiment_id"])),
            "date_start": date_start.strftime("%Y-%m-%d"),
            "date_end": date_end.strftime("%Y-%m-%d"),
            "attribution_window_days": CRM_ATTRIBUTION_WINDOW_DAYS,
            "attribution_window_seconds": attribution_window_seconds(exp_info),
        },
        config=cfg,
    )
    logger.info("crm email metrics query:\n%s", query)
    return execute_sql(query)
