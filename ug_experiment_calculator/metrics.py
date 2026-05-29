from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import scipy.special as special
import scipy.stats as scipy_stats
import yaml


def fill_missing_variations_by_date(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["dt"] = pd.to_datetime(df["dt"])

    all_dates = pd.date_range(df["dt"].min(), df["dt"].max(), freq="D")
    all_variations = df["variation"].dropna().unique()

    full_index = pd.MultiIndex.from_product(
        [all_dates, all_variations],
        names=["dt", "variation"],
    )

    df = df.set_index(["dt", "variation"]).reindex(full_index).reset_index()

    value_cols = [col for col in df.columns if col not in ["dt", "variation"]]
    df[value_cols] = df[value_cols].fillna(0)

    return df


def calc_cumulative_aggregates(df: pd.DataFrame) -> pd.DataFrame:
    df = fill_missing_variations_by_date(df)
    df = df.copy()
    df["dt"] = pd.to_datetime(df["dt"])

    var_config = {
        "arpu_var": {
            "count_col": "members",
            "revenue_col": "revenue",
        },
        "lifetime_arpu_var": {
            "count_col": "members",
            "revenue_col": "lifetime_revenue",
        },
        "arppu_var": {
            "count_col": "buyer_cnt",
            "revenue_col": "revenue",
        },
        "subscriptions_per_user_var": {
            "count_col": "members",
            "revenue_col": "subscriptions_cnt",
        },
    }

    var_cols = set(var_config.keys())
    regular_cols = [col for col in df.columns if col not in ["dt", "variation"] and col not in var_cols]
    result_parts = []

    for variation, group in df.sort_values(["variation", "dt"]).groupby("variation"):
        group = group.copy()
        group[regular_cols] = group[regular_cols].cumsum()
        original_group = df[df["variation"] == variation].sort_values("dt").copy()

        for var_col, cfg in var_config.items():
            count_col = cfg["count_col"]
            revenue_col = cfg["revenue_col"]
            cumulative_vars = []

            prev_count = 0
            prev_revenue = 0
            prev_var = 0

            for _, row in original_group.iterrows():
                count = row[count_col]
                revenue = row[revenue_col]
                current_var = row[var_col]

                if pd.isna(current_var):
                    current_var = 0

                total_count = prev_count + count
                total_revenue = prev_revenue + revenue

                if total_count <= 1:
                    cumulative_var = 0
                elif prev_count == 0:
                    cumulative_var = current_var
                else:
                    prev_mean = prev_revenue / prev_count if prev_count else 0
                    current_mean = revenue / count if count else 0
                    total_mean = total_revenue / total_count if total_count else 0

                    cumulative_var = (
                        (prev_count - 1) * prev_var
                        + (count - 1) * current_var
                        + prev_count * (prev_mean - total_mean) ** 2
                        + count * (current_mean - total_mean) ** 2
                    ) / (total_count - 1)

                cumulative_vars.append(cumulative_var)
                prev_count = total_count
                prev_revenue = total_revenue
                prev_var = cumulative_var

            group[var_col] = cumulative_vars

        result_parts.append(group)

    result = pd.concat(result_parts, ignore_index=True)
    result = result.sort_values(["variation", "dt"]).reset_index(drop=True)
    result["dt"] = result["dt"].dt.strftime("%Y-%m-%d")

    return result


def normalize_metric_config(metric_items: list[dict]) -> dict:
    config = {}
    for item in metric_items:
        config.update(item)
    return config


def calc_stats(mean_0, mean_1, var_0, var_1, len_0, len_1, alpha=None, required_power=None, pvalue=None, calc_mean=False):
    if math.isnan(mean_0) or math.isnan(mean_1) or math.isnan(len_0) or math.isnan(len_1):
        return {
            "pvalue": 1,
            "cohen_d": 0,
            "ci": [np.array([0, 0])],
        }
    if alpha is None:
        alpha = 0.05
    if required_power is None:
        required_power = 0.8

    std = np.sqrt(var_0 / len_0 + var_1 / len_1)
    mean_abs = abs(mean_1 - mean_0)
    mean = mean_1 - mean_0
    sd = np.sqrt((var_0 * len_0 + var_1 * len_1) / (len_0 + len_1 - 2))

    if pvalue is None:
        pvalue = scipy_stats.norm.cdf(x=0, loc=mean_abs, scale=std) * 2
    elif not calc_mean:
        std_corrected = np.abs(special.nrdtrisd(0, pvalue / 2, mean_abs))
        sd *= 1 + (std_corrected - std) / std
        std = std_corrected
    else:
        mean_abs = special.nrdtrimn(pvalue / 2, std, 0)
        mean = mean_abs
        if mean_0 > mean_1:
            mean *= -1

    cohen_d = mean_abs / sd

    return {
        "pvalue": pvalue,
        "cohen_d": cohen_d,
        "ci": [
            np.array([
                scipy_stats.norm.ppf(alpha / 2, mean, std),
                scipy_stats.norm.ppf(1 - alpha / 2, mean, std),
            ])
        ],
    }


def safe_divide(numerator, denominator):
    if pd.isna(denominator) or denominator == 0:
        return np.nan
    return numerator / denominator


def load_metrics_config(metrics_yaml_path: str | Path) -> dict[str, Any]:
    with Path(metrics_yaml_path).open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def calc_metrics_stats_by_variation_pairs(
    cumulative_df: pd.DataFrame,
    metrics_yaml_path: str | Path,
    control_variation: int = 1,
    client: str = "",
) -> pd.DataFrame:
    df = cumulative_df.copy()
    df["dt"] = pd.to_datetime(df["dt"])
    metrics_config = load_metrics_config(metrics_yaml_path)
    all_variations = sorted(df["variation"].unique())
    test_variations = [variation for variation in all_variations if variation != control_variation]
    result_rows = []

    for metric_name, metric_items in metrics_config.items():
        metric_config = normalize_metric_config(metric_items)

        numerator_col = metric_config.get("numerator")
        denominator_col = metric_config.get("denominator")
        variance_col = metric_config.get("variance")
        distribution = metric_config.get("distribution")
        is_percentage = metric_config.get("percentage", False)
        calc_platform = metric_config.get("platforms", [])
        if client not in calc_platform:
            continue

        required_cols = {"dt", "variation", numerator_col, denominator_col}
        if variance_col:
            required_cols.add(variance_col)

        if not required_cols.issubset(df.columns):
            continue

        for current_dt in sorted(df["dt"].unique()):
            date_df = df[df["dt"] == current_dt]
            control_rows = date_df[date_df["variation"] == control_variation]

            if control_rows.empty:
                continue

            control_row = control_rows.iloc[0]
            numerator_0 = control_row[numerator_col]
            denominator_0 = control_row[denominator_col]
            mean_0 = safe_divide(numerator_0, denominator_0)
            len_0 = denominator_0

            if pd.isna(mean_0) or pd.isna(len_0) or len_0 <= 0:
                continue

            if variance_col:
                var_0 = control_row[variance_col]
                if pd.isna(var_0):
                    var_0 = 0
            elif distribution == "bernoulli":
                var_0 = mean_0 * (1 - mean_0)
            else:
                continue

            for test_variation in test_variations:
                test_rows = date_df[date_df["variation"] == test_variation]
                if test_rows.empty:
                    continue

                test_row = test_rows.iloc[0]
                numerator_1 = test_row[numerator_col]
                denominator_1 = test_row[denominator_col]
                mean_1 = safe_divide(numerator_1, denominator_1)
                len_1 = denominator_1

                if pd.isna(mean_1) or pd.isna(len_1) or len_1 <= 0:
                    continue

                if variance_col:
                    var_1 = test_row[variance_col]
                    if pd.isna(var_1):
                        var_1 = 0
                elif distribution == "bernoulli":
                    var_1 = mean_1 * (1 - mean_1)
                else:
                    continue

                stats = calc_stats(
                    mean_0=mean_0,
                    mean_1=mean_1,
                    var_0=var_0,
                    var_1=var_1,
                    len_0=len_0,
                    len_1=len_1,
                )

                mean_diff = mean_1 - mean_0
                ci = stats["ci"]
                coefficient = 100 if is_percentage else 1

                result_rows.append({
                    "dt": current_dt,
                    "metric": metric_name,
                    "variation_pair": f"{control_variation} vs {test_variation}",
                    "control_variation": control_variation,
                    "test_variation": test_variation,
                    "mean_0": mean_0 * coefficient,
                    "mean_1": mean_1 * coefficient,
                    "mean_diff": mean_diff * coefficient,
                    "lift": mean_diff / mean_0 * 100 if mean_0 != 0 else 0,
                    "ci_low": ci[0][0] * coefficient,
                    "ci_high": ci[0][1] * coefficient,
                    "pvalue": stats["pvalue"],
                    "numerator": numerator_col,
                    "denominator": denominator_col,
                    "variance": variance_col,
                    "distribution": distribution,
                    "percentage": is_percentage,
                })

    result = pd.DataFrame(result_rows)
    if not result.empty:
        result["dt"] = result["dt"].dt.strftime("%Y-%m-%d")

    return result
