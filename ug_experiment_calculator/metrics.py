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
        "charges_per_user_var": {
            "count_col": "members",
            "revenue_col": "charge_cnt",
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


FUNNEL_ID_COLUMNS = [
    "funnel_definition_key",
    "funnel_definition_name",
    "funnel_definition_description",
    "funnel_key",
    "funnel_name",
    "transition_key",
    "transition_name",
    "from_step_key",
    "from_step_name",
    "from_step_order",
    "to_step_key",
    "to_step_name",
    "to_step_order",
]


def calc_cumulative_funnel_aggregates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if df.empty:
        df["conversion"] = pd.Series(dtype="float64")
        return df

    for column in ("funnel_definition_key", "funnel_definition_name", "funnel_definition_description"):
        if column not in df.columns:
            df[column] = ""

    df["dt"] = pd.to_datetime(df["dt"])
    df["denominator_users"] = pd.to_numeric(df["denominator_users"], errors="coerce").fillna(0)
    df["numerator_users"] = pd.to_numeric(df["numerator_users"], errors="coerce").fillna(0)

    all_dates = pd.date_range(df["dt"].min(), df["dt"].max(), freq="D")
    all_variations = df["variation"].dropna().unique()
    transitions = df[FUNNEL_ID_COLUMNS].drop_duplicates().reset_index(drop=True)
    transitions = transitions.reset_index(names="transition_index")

    full_index = pd.MultiIndex.from_product(
        [all_dates, all_variations, transitions["transition_index"]],
        names=["dt", "variation", "transition_index"],
    ).to_frame(index=False)

    full_df = full_index.merge(transitions, on="transition_index", how="left").drop(columns=["transition_index"])
    full_df = full_df.merge(
        df,
        on=["dt", "variation", *FUNNEL_ID_COLUMNS],
        how="left",
    )
    full_df[["denominator_users", "numerator_users"]] = full_df[["denominator_users", "numerator_users"]].fillna(0)

    sort_columns = ["variation", "funnel_definition_key", "funnel_key", "from_step_order", "to_step_order", "dt"]
    full_df = full_df.sort_values(sort_columns).reset_index(drop=True)
    group_columns = ["variation", *FUNNEL_ID_COLUMNS]
    full_df[["denominator_users", "numerator_users"]] = full_df.groupby(group_columns, dropna=False)[
        ["denominator_users", "numerator_users"]
    ].cumsum()
    full_df[["denominator_users", "numerator_users"]] = full_df[
        ["denominator_users", "numerator_users"]
    ].astype("int64")
    full_df["conversion"] = full_df["numerator_users"] / full_df["denominator_users"].replace({0: np.nan})
    full_df["dt"] = full_df["dt"].dt.strftime("%Y-%m-%d")

    return full_df


def normalize_metric_config(metric_items: list[dict]) -> dict:
    config = {}
    for item in metric_items:
        config.update(item)
    return config


def _parse_config_value(value: object) -> object:
    if isinstance(value, str):
        for parser in (yaml.safe_load,):
            try:
                parsed = parser(value)
            except Exception:
                continue
            if parsed is not None:
                return parsed
    return value


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


def platform_buckets_for_context(client: str, segment: dict | None = None, clients_options: object = "") -> set[str]:
    if client != "UG_WEB":
        return {"all"}

    segment = segment or {}
    explicit_platform = segment.get("platform")
    if explicit_platform is not None:
        platform_values = _flatten_option_values(explicit_platform)
    else:
        parsed_options = _parse_config_value(clients_options)
        if isinstance(parsed_options, dict):
            parsed_options = parsed_options.get(client, {})
        platform_values = _collect_platform_values(parsed_options)

    if not platform_values:
        return {"mobile"}

    result = set()
    for value in platform_values:
        text = str(value).strip().lower()
        if text in {"all"}:
            result.add("all")
        elif text in {"desktop", "web"}:
            result.add("desktop")
        elif text in {"mobile", "mobweb", "mobile_web", "mobile web", "mweb"}:
            result.add("mobile")
        elif text in {"phone"}:
            result.update({"mobile", "phone"})
        elif text in {"tablet"}:
            result.update({"mobile", "tablet"})
        else:
            try:
                platform_id = int(text)
            except ValueError:
                continue
            if platform_id == 1:
                result.add("desktop")
            elif platform_id == 2:
                result.update({"mobile", "phone"})
            elif platform_id == 3:
                result.update({"mobile", "tablet"})
            elif platform_id > 1:
                result.add("mobile")

    return result or {"mobile"}


def config_enabled_for_context(
    config: dict,
    client: str,
    *,
    segment: dict | None = None,
    clients_options: object = "",
) -> bool:
    sources = config.get("sources", config.get("platforms", []))
    if sources and client not in sources:
        return False

    platforms = config.get("platforms", ["all"])
    if "all" in platforms:
        return True

    current_platforms = platform_buckets_for_context(client, segment=segment, clients_options=clients_options)
    return bool(current_platforms.intersection(set(platforms)))


def metric_columns_for_client(
    metrics_yaml_path: str | Path,
    client: str,
    *,
    segment: dict | None = None,
    clients_options: object = "",
) -> set[str]:
    metrics_config = load_metrics_config(metrics_yaml_path)
    columns = set()

    for metric_items in metrics_config.values():
        metric_config = normalize_metric_config(metric_items)
        if not config_enabled_for_context(metric_config, client, segment=segment, clients_options=clients_options):
            continue

        for key in ("numerator", "denominator", "variance"):
            value = metric_config.get(key)
            if value:
                columns.add(value)

    return columns


def stats_columns_for_client(
    stats_yaml_path: str | Path,
    client: str,
    *,
    segment: dict | None = None,
    clients_options: object = "",
) -> set[str]:
    stats_config = load_metrics_config(stats_yaml_path)
    columns = set()

    for stat_name, stat_items in stats_config.items():
        stat_config = normalize_metric_config(stat_items)
        table_position = int(stat_config.get("table_position") or 0)
        if table_position <= 0:
            continue

        if not config_enabled_for_context(stat_config, client, segment=segment, clients_options=clients_options):
            continue

        columns.add(str(stat_name))

    return columns


def load_funnels_config(funnels_yaml_path: str | Path) -> dict[str, Any]:
    with Path(funnels_yaml_path).open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def normalize_funnel_config(funnel_items: list[dict]) -> dict:
    config = {}
    for item in funnel_items:
        config.update(item)
    return config


def funnel_platforms(funnel_config: dict) -> list[str]:
    conditions = funnel_config.get("conditions", {})
    return funnel_config.get("platforms") or conditions.get("platforms", [])


def _funnel_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def funnel_calculation_enabled(funnel_config: dict) -> bool:
    return _funnel_bool(
        funnel_config.get(
            "enabled",
            funnel_config.get("calculate", funnel_config.get("calculate_funnel")),
        ),
        default=False,
    )


def funnel_enabled_for_client(funnel_config: dict, client: str) -> bool:
    platforms = funnel_platforms(funnel_config)
    return funnel_calculation_enabled(funnel_config) and client in platforms


def calc_stats(mean_0, mean_1, var_0, var_1, len_0, len_1, alpha=None, required_power=None, pvalue=None, calc_mean=False):
    mean_0 = _finite_number_or_none(mean_0)
    mean_1 = _finite_number_or_none(mean_1)
    var_0 = _finite_number_or_none(var_0)
    var_1 = _finite_number_or_none(var_1)
    len_0 = _finite_number_or_none(len_0)
    len_1 = _finite_number_or_none(len_1)

    if mean_0 is None or mean_1 is None or len_0 is None or len_1 is None:
        return _neutral_stats_result()
    if var_0 is None or var_1 is None:
        return _neutral_stats_result()
    if len_0 <= 0 or len_1 <= 0:
        return _neutral_stats_result()

    if alpha is None:
        alpha = 0.05
    if required_power is None:
        required_power = 0.8

    var_0 = max(0, var_0)
    var_1 = max(0, var_1)

    mean_abs = abs(mean_1 - mean_0)
    mean = mean_1 - mean_0
    std = math.sqrt(var_0 / len_0 + var_1 / len_1)
    sd = _pooled_standard_deviation(var_0, var_1, len_0, len_1)

    if std == 0:
        if pvalue is None:
            pvalue = 1.0 if mean_abs == 0 else 0.0
        else:
            pvalue = _normalize_pvalue(pvalue)
        return {
            "pvalue": pvalue,
            "cohen_d": _cohen_d(mean_abs, sd),
            "ci": [np.array([mean, mean])],
        }

    if pvalue is None:
        pvalue = scipy_stats.norm.cdf(x=0, loc=mean_abs, scale=std) * 2
    elif not calc_mean:
        pvalue = _normalize_pvalue(pvalue)
        if 0 < pvalue < 1:
            std_corrected = _finite_number_or_none(np.abs(special.nrdtrisd(0, pvalue / 2, mean_abs)))
            if std_corrected is not None and std_corrected > 0:
                if sd is not None:
                    sd *= 1 + (std_corrected - std) / std
                std = std_corrected
    else:
        pvalue = _normalize_pvalue(pvalue)
        if 0 < pvalue < 1:
            inferred_mean_abs = _finite_number_or_none(special.nrdtrimn(pvalue / 2, std, 0))
            if inferred_mean_abs is not None:
                mean_abs = inferred_mean_abs
                mean = mean_abs
                if mean_0 > mean_1:
                    mean *= -1

    pvalue = _normalize_pvalue(pvalue)

    return {
        "pvalue": pvalue,
        "cohen_d": _cohen_d(mean_abs, sd),
        "ci": [
            np.array([
                scipy_stats.norm.ppf(alpha / 2, mean, std),
                scipy_stats.norm.ppf(1 - alpha / 2, mean, std),
            ])
        ],
    }


def _neutral_stats_result() -> dict:
    return {
        "pvalue": 1,
        "cohen_d": 0,
        "ci": [np.array([0, 0])],
    }


def _finite_number_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        number_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number_value):
        return None
    return number_value


def _pooled_standard_deviation(var_0: float, var_1: float, len_0: float, len_1: float) -> float | None:
    denominator = len_0 + len_1 - 2
    if denominator <= 0:
        return None

    value = (var_0 * len_0 + var_1 * len_1) / denominator
    if value <= 0:
        return 0
    return math.sqrt(value)


def _cohen_d(mean_abs: float, sd: float | None) -> float:
    if sd is None or sd <= 0:
        return 0
    return mean_abs / sd


def _normalize_pvalue(value) -> float:
    pvalue = _finite_number_or_none(value)
    if pvalue is None:
        return 1
    return min(1, max(0, pvalue))


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
    segment: dict | None = None,
    clients_options: object = "",
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
        if not config_enabled_for_context(metric_config, client, segment=segment, clients_options=clients_options):
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


def calc_funnel_stats_by_variation_pairs(
    cumulative_df: pd.DataFrame,
    control_variation: int = 1,
) -> pd.DataFrame:
    df = cumulative_df.copy()
    if df.empty:
        return pd.DataFrame()

    df["dt"] = pd.to_datetime(df["dt"])
    all_variations = sorted(df["variation"].unique())
    test_variations = [variation for variation in all_variations if variation != control_variation]
    result_rows = []

    for group_key, date_df in df.groupby(["dt", *FUNNEL_ID_COLUMNS], dropna=False):
        current_dt = group_key[0]
        funnel_values = dict(zip(FUNNEL_ID_COLUMNS, group_key[1:]))
        control_rows = date_df[date_df["variation"] == control_variation]

        if control_rows.empty:
            continue

        control_row = control_rows.iloc[0]
        denominator_0 = control_row["denominator_users"]
        numerator_0 = control_row["numerator_users"]
        mean_0 = safe_divide(numerator_0, denominator_0)
        len_0 = denominator_0

        if pd.isna(mean_0) or pd.isna(len_0) or len_0 <= 0:
            continue

        var_0 = mean_0 * (1 - mean_0)

        for test_variation in test_variations:
            test_rows = date_df[date_df["variation"] == test_variation]
            if test_rows.empty:
                continue

            test_row = test_rows.iloc[0]
            denominator_1 = test_row["denominator_users"]
            numerator_1 = test_row["numerator_users"]
            mean_1 = safe_divide(numerator_1, denominator_1)
            len_1 = denominator_1

            if pd.isna(mean_1) or pd.isna(len_1) or len_1 <= 0:
                continue

            var_1 = mean_1 * (1 - mean_1)
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

            result_rows.append({
                "dt": current_dt,
                **funnel_values,
                "variation_pair": f"{control_variation} vs {test_variation}",
                "control_variation": control_variation,
                "test_variation": test_variation,
                "control_denominator": denominator_0,
                "control_numerator": numerator_0,
                "test_denominator": denominator_1,
                "test_numerator": numerator_1,
                "mean_0": mean_0 * 100,
                "mean_1": mean_1 * 100,
                "mean_diff": mean_diff * 100,
                "lift": mean_diff / mean_0 * 100 if mean_0 != 0 else 0,
                "ci_low": ci[0][0] * 100,
                "ci_high": ci[0][1] * 100,
                "pvalue": stats["pvalue"],
                "distribution": "bernoulli",
                "percentage": True,
            })

    result = pd.DataFrame(result_rows)
    if not result.empty:
        result["dt"] = result["dt"].dt.strftime("%Y-%m-%d")

    return result
