"""CRM read metrics the stats tables do not hold, computed on demand from a calculated CRM cohort.

They read the cohort the calculator already stored (``exp_users_<id>``) and the calculator's own
subscription SQL inlined as a subquery, so they create no tables and agree with ``ug_exp_stats``
by construction. Each function returns a long DataFrame; presentation is left to the caller.

- ``get_crm_extra_metrics``: revenue per 1000 delivered (the headline of the CRM Slack post) and
  clicks -> charges (the After Click conversion), with delta-method z-tests against control.
- ``get_crm_landing_funnel``: the paywall funnel of the send (Landing Open -> Instant Pay ->
  emergency offer -> checkout -> purchase click -> access), by real device, with z-tests.
- ``get_crm_payment_mix``: charged subscriptions by payment method, with a chi-square test.

The experiment must have been calculated first (``calculate_exp_info``): the cohort is read, not
rebuilt.
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from clickhouse_worker import clickhouse_string_literal as _clickhouse_string_literal
from clickhouse_worker import execute_sql
import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from .config import ExperimentCalculatorConfig
from .crm import (
    CRM_ATTRIBUTION_WINDOW_DAYS,
    CRM_SEGMENT_ALL,
    CRM_SEGMENT_CRM,
    UG_CRM_CLIENT,
    attribution_window_seconds,
    crm_subscription_sql,
    is_crm_exp_info,
    source_filter_sql,
    subscription_window_end,
)
from .metrics import calc_stats


logger = logging.getLogger(__name__)

CONTROL_VARIATION = 1
FINAL_SELECT_MARKER = "\nselect\n"

LANDING_STEPS = [
    "landing_open",
    "instant_pay_available",
    "emergency_offer_shown",
    "emergency_to_checkout",
    "checkout_view",
    "purchase_click",
    "access",
]
# (conversion name, numerator step, denominator step), as on the CRM page template
LANDING_CONVERSIONS = [
    ("open -> instant pay, %", "instant_pay_available", "landing_open"),
    ("open -> emergency offer, %", "emergency_offer_shown", "landing_open"),
    ("emergency -> checkout, %", "emergency_to_checkout", "emergency_offer_shown"),
    ("open -> checkout view, %", "checkout_view", "landing_open"),
    ("checkout view -> purchase click, %", "purchase_click", "checkout_view"),
    ("checkout view -> access, %", "access", "checkout_view"),
    ("open -> access, %", "access", "landing_open"),
]


def get_config(config: Optional[ExperimentCalculatorConfig] = None) -> ExperimentCalculatorConfig:
    return config or ExperimentCalculatorConfig.from_env()


def _crm_context(exp_id, segment_name: str, config: ExperimentCalculatorConfig) -> dict:
    from .repository import experiment_storage_id, get_experiment, get_experiment_users_hash

    exp_info = get_experiment(exp_id, config=config)
    if not is_crm_exp_info(exp_info):
        raise ValueError(f"Experiment {exp_id} is not a CRM (Bloomreach, provider_id = 1) experiment")
    if segment_name not in exp_info["segments"]:
        raise ValueError(f"Segment {segment_name!r} is not configured for exp {exp_id}: {list(exp_info['segments'])}")
    segment = exp_info["segments"][segment_name]
    date_start = datetime.datetime.fromtimestamp(int(exp_info["date_start"]), datetime.timezone.utc)
    date_end = datetime.datetime.fromtimestamp(int(exp_info["date_end"]), datetime.timezone.utc)
    return {
        "exp_info": exp_info,
        "segment_name": segment_name,
        "segment": segment,
        "segment_hash": get_experiment_users_hash(exp_info, UG_CRM_CLIENT, segment),
        "users_table": config.full_table(f"exp_users_{experiment_storage_id(exp_info)}"),
        "date_start": date_start,
        "date_end": date_end,
    }


def _common_params(ctx: dict) -> dict:
    exp_info = ctx["exp_info"]
    return {
        "exp_users_table": ctx["users_table"],
        "client_sql": _clickhouse_string_literal(UG_CRM_CLIENT),
        "segment_sql": _clickhouse_string_literal(ctx["segment_name"]),
        "segment_hash_sql": _clickhouse_string_literal(ctx["segment_hash"]),
        "provider_experiment_id_sql": _clickhouse_string_literal(str(exp_info["provider_experiment_id"])),
        "date_start": ctx["date_start"].strftime("%Y-%m-%d"),
        "date_end": ctx["date_end"].strftime("%Y-%m-%d"),
        "attribution_window_days": CRM_ATTRIBUTION_WINDOW_DAYS,
        "attribution_window_seconds": attribution_window_seconds(exp_info),
    }


def render_member_ctes_sql(ctx: dict, config: ExperimentCalculatorConfig) -> str:
    """The CTEs of monetization_metrics.sql with the joined subscriptions inlined, plus the
    per-member CTEs of crm_member_ctes.sql. Rendered exactly as the calculation renders them,
    so a member's purchases and revenue are the ones the stats table summed."""
    from .repository import TRIAL_CONVERSION_MODEL_TABLE, get_query

    exp_info = ctx["exp_info"]
    segment = ctx["segment"]
    subs_end = subscription_window_end(exp_info, ctx["date_end"])
    subscriptions_sql = get_query(
        "subscriptions_joined_by_sub_date",
        params={
            "date_start": ctx["date_start"].strftime("%Y-%m-%d"),
            "date_end": subs_end.strftime("%Y-%m-%d"),
            "where_sql": segment.get("swf", "1"),
            "having_sql": segment.get("shf", "1"),
            "subscriptions_table": config.subscriptions_table,
            "transactions_table": config.subscription_transactions_table,
            "source_filter_sql": source_filter_sql(exp_info),
            "crm_subscription_sql": crm_subscription_sql(exp_info),
        },
        config=config,
    )
    common = _common_params(ctx)
    metrics_sql = get_query(
        "monetization_metrics",
        params={
            "exp_users_table": common["exp_users_table"],
            "subscription_table": f"(\n{subscriptions_sql}\n)",
            "client_sql": common["client_sql"],
            "segment_sql": common["segment_sql"],
            "segment_hash_sql": common["segment_hash_sql"],
            "trial_conversion_model_table": config.full_table(TRIAL_CONVERSION_MODEL_TABLE),
            "attribution_window_seconds": common["attribution_window_seconds"],
        },
        config=config,
    )
    cut = metrics_sql.rfind(FINAL_SELECT_MARKER)
    prefix = metrics_sql[:cut].rstrip()
    if cut < 0 or not prefix.endswith(")") or "`subscription_matches_with_expected` as (" not in prefix or "`exp_users` as (" not in prefix:
        raise RuntimeError("monetization_metrics.sql changed shape: its CTEs can no longer be reused by crm_metrics")
    member_ctes = get_query("crm_member_ctes", params=common, config=config)
    return prefix + ",\n" + member_ctes.rstrip()


def _ratio_moments(n: float, sx: float, sxx: float, sy: float, syy: float, sxy: float) -> tuple[float, float]:
    """Ratio R = sum(x) / sum(y) over n members and its per-member delta-method variance,
    so that Var(R) = var / n (the shape calc_stats expects)."""
    if n <= 0 or sy <= 0:
        return float("nan"), float("nan")
    mx, my = sx / n, sy / n
    vx = sxx / n - mx * mx
    vy = syy / n - my * my
    cxy = sxy / n - mx * my
    ratio = mx / my
    var = (vx - 2 * ratio * cxy + ratio * ratio * vy) / (my * my)
    return ratio, max(var, 0.0)


def _pairwise(rows: list[dict], metric: str, scale: float, money: bool) -> list[dict]:
    control = next((row for row in rows if row["variation"] == CONTROL_VARIATION), None)
    out = []
    for row in rows:
        result = {
            "metric": metric,
            "variation": row["variation"],
            "value": row["mean"] * scale,
            "numerator": row["numerator"],
            "denominator": row["denominator"],
            "diff": np.nan,
            "lift, %": np.nan,
            "pvalue": np.nan,
        }
        if control is not None and row["variation"] != CONTROL_VARIATION:
            stats = calc_stats(
                mean_0=control["mean"],
                mean_1=row["mean"],
                var_0=control["var"],
                var_1=row["var"],
                len_0=control["len"],
                len_1=row["len"],
            )
            result["diff"] = (row["mean"] - control["mean"]) * scale
            result["lift, %"] = (row["mean"] / control["mean"] - 1) * 100 if control["mean"] else np.nan
            result["pvalue"] = stats["pvalue"]
        result["is_money"] = money
        out.append(result)
    return out


def build_crm_extra_metrics(sums: pd.DataFrame) -> pd.DataFrame:
    """Pure part of get_crm_extra_metrics: per-variation sums -> metric rows with z-tests."""
    revenue_rows, clicks_rows = [], []
    for row in sums.sort_values("variation").to_dict("records"):
        n = float(row["members"])
        revenue, revenue_var = _ratio_moments(
            n,
            float(row["revenue"]),
            float(row["revenue_sq"]),
            float(row["delivered_cnt"]),
            float(row["delivered_cnt"]),  # a 0/1 flag: sum of squares = sum
            float(row["revenue_x_delivered"]),
        )
        revenue_rows.append(
            {"variation": int(row["variation"]), "mean": revenue, "var": revenue_var, "len": n,
             "numerator": float(row["revenue"]), "denominator": float(row["delivered_cnt"])}
        )
        charges, charges_var = _ratio_moments(
            n,
            float(row["charge_cnt"]),
            float(row["charge_sq"]),
            float(row["clicked_cnt"]),
            float(row["clicked_cnt"]),
            float(row["charge_x_clicked"]),
        )
        clicks_rows.append(
            {"variation": int(row["variation"]), "mean": charges, "var": charges_var, "len": n,
             "numerator": float(row["charge_cnt"]), "denominator": float(row["clicked_cnt"])}
        )
    rows = _pairwise(revenue_rows, "revenue per 1000 delivered, $", 1000, money=True)
    rows += _pairwise(clicks_rows, "clicks -> charges, %", 100, money=False)
    return pd.DataFrame(rows)


def get_crm_extra_metrics(
    exp_id,
    *,
    segment: str = CRM_SEGMENT_CRM,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    """Revenue per 1000 delivered and clicks -> charges for one CRM segment.

    Revenue and charges are the segment's (net revenue after commissions and 14-day refunds,
    as in ug_exp_stats); delivered / clicked are recipients of the campaign within the
    attribution window. Both are ratios of member-level sums, tested with the delta method.
    Use segment "CRM purchases" for After Split and "Campaign purchases" for After Click.
    """
    cfg = get_config(config)
    ctx = _crm_context(exp_id, segment, cfg)
    query = render_member_ctes_sql(ctx, cfg) + "\n" + _final_sql("crm_member_metrics", ctx, cfg)
    logger.info("crm extra metrics query:\n%s", query)
    return build_crm_extra_metrics(execute_sql(query))


def build_crm_payment_mix(counts: pd.DataFrame) -> pd.DataFrame:
    """Pure part of get_crm_payment_mix: shares per variation and one chi-square p-value
    for "the mix differs between control and each test variation"."""
    if counts.empty:
        return pd.DataFrame(columns=["variation", "payment_method", "charged_subscriptions", "share, %", "pvalue"])
    table = counts.pivot_table(index="payment_method", columns="variation", values="charged_subscriptions", fill_value=0)
    result = counts.copy()
    totals = result.groupby("variation")["charged_subscriptions"].transform("sum")
    result["share, %"] = result["charged_subscriptions"] / totals * 100
    result["pvalue"] = np.nan
    if CONTROL_VARIATION in table.columns:
        for variation in table.columns:
            if variation == CONTROL_VARIATION:
                continue
            pair = table[[CONTROL_VARIATION, variation]]
            pair = pair[pair.sum(axis=1) > 0]
            if len(pair) < 2:
                continue
            pvalue = scipy_stats.chi2_contingency(pair.to_numpy())[1]
            result.loc[result["variation"] == variation, "pvalue"] = pvalue
    return result.sort_values(["variation", "charged_subscriptions"], ascending=[True, False]).reset_index(drop=True)


def get_crm_payment_mix(
    exp_id,
    *,
    segment: str = CRM_SEGMENT_CRM,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    """Charged subscriptions of the segment by the payment method of their first charge
    (ug_subscriptions_events.payment_method: CreditCard / PayPal / Apple Pay / Google Pay)."""
    cfg = get_config(config)
    ctx = _crm_context(exp_id, segment, cfg)
    query = render_member_ctes_sql(ctx, cfg) + "\n" + _final_sql("crm_payment_mix", ctx, cfg)
    logger.info("crm payment mix query:\n%s", query)
    return build_crm_payment_mix(execute_sql(query))


def build_crm_landing_funnel(counts: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Pure part of get_crm_landing_funnel.

    Returns ``users`` (step users per platform and variation; platform "all" = desktop +
    mobile) and ``conversion`` (each conversion per platform and variation with lift and
    the z-test p-value against control)."""
    if counts.empty:
        return {"users": pd.DataFrame(columns=["platform", "variation", *LANDING_STEPS]), "conversion": pd.DataFrame()}
    by_platform = counts[["platform", "variation", *LANDING_STEPS]].copy()
    total = by_platform.groupby("variation", as_index=False)[LANDING_STEPS].sum()
    total.insert(0, "platform", "all")
    users = pd.concat([total, by_platform], ignore_index=True)
    users["platform"] = pd.Categorical(users["platform"], ["all", "desktop", "mobile"], ordered=True)
    users = users.sort_values(["platform", "variation"]).reset_index(drop=True)
    users["platform"] = users["platform"].astype(str)

    rows = []
    for platform, grp in users.groupby("platform", sort=False):
        for name, numerator, denominator in LANDING_CONVERSIONS:
            step_rows = []
            for record in grp.to_dict("records"):
                den = float(record[denominator])
                mean = float(record[numerator]) / den if den > 0 else float("nan")
                step_rows.append(
                    {"variation": int(record["variation"]), "mean": mean,
                     "var": mean * (1 - mean) if den > 0 else float("nan"), "len": den,
                     "numerator": float(record[numerator]), "denominator": den}
                )
            for row in _pairwise(step_rows, name, 100, money=False):
                row["platform"] = platform
                rows.append(row)
    conversion = pd.DataFrame(rows)[["platform", "metric", "variation", "value", "numerator", "denominator", "diff", "lift, %", "pvalue"]]
    return {"users": users, "conversion": conversion}


def get_crm_landing_funnel(
    exp_id,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> dict[str, pd.DataFrame]:
    """Paywall funnel of the send's landing visitors, overall and by real device.

    A landing visitor is a member who opened the CRM landing ('Landing Upgrade Open') from a
    campaign of this send (utm_campaign among the campaigns its recipients clicked) within the
    attribution window; later steps count from that first open. Only meaningful when the test
    touches the paywall; an email-only test shows the same funnel in every variation.
    """
    cfg = get_config(config)
    ctx = _crm_context(exp_id, CRM_SEGMENT_ALL, cfg)
    campaigns = ctx["exp_info"].get("crm_clicked_utm_campaigns") or []
    params = _common_params(ctx) | {
        "utm_campaigns_sql": ", ".join(_clickhouse_string_literal(value) for value in campaigns) or "''",
    }
    from .repository import get_query

    query = get_query("crm_landing_funnel", params=params, config=cfg)
    logger.info("crm landing funnel query:\n%s", query)
    return build_crm_landing_funnel(execute_sql(query))


def get_crm_landing_steps(
    exp_id,
    *,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> pd.DataFrame:
    """Step-by-step landing funnel counts per variation and device (crm_landing_steps.sql):
    where the visitors bought (paywall / checkout / emergency offer) and where the others left,
    in the layout of the CRM page template. Input of ``crm_report``'s landing tables."""
    cfg = get_config(config)
    ctx = _crm_context(exp_id, CRM_SEGMENT_ALL, cfg)
    campaigns = ctx["exp_info"].get("crm_clicked_utm_campaigns") or []
    params = _common_params(ctx) | {
        "utm_campaigns_sql": ", ".join(_clickhouse_string_literal(value) for value in campaigns) or "''",
    }
    from .repository import get_query

    query = get_query("crm_landing_steps", params=params, config=cfg)
    logger.info("crm landing steps query:\n%s", query)
    return execute_sql(query)


def _final_sql(query_name: str, ctx: dict, config: ExperimentCalculatorConfig) -> str:
    from .repository import get_query

    return get_query(query_name, params=_common_params(ctx), config=config)
