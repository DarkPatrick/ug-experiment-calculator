"""Confluence output for bandit (aix) experiments: the two-read report.

Read 1 is the wrapping admin A/B experiment, rendered by the ordinary
calculator machinery — the only layer where significance testing is valid.
Read 2 is the per-arm descriptive table: arms as columns (control first, then
top arms by participants, the long tail grouped into one bucket so a large
pool stays readable), metrics as rows, with every arm's participants,
exposure window and aix exposures shown next to its rates. Per-arm p-values
are suppressed by design — bandit arm sizes are endogenous to performance.
"""

from __future__ import annotations

import datetime
from html import escape
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .colors import HEADER_COLOR
from .config import ExperimentCalculatorConfig
from .confluence_tables import (
    _cell,
    _heading,
    _load_metric_table_configs,
    _paragraph,
    _row,
    _row_header_cell,
    _strong_text,
    _table,
    _ui_expand,
    get_experiment_confluence_table_code,
    get_experiment_stats_confluence_table_data,
    get_experiment_stats_confluence_table_code,
)
from .value_formatting import (
    format_diff_percent,
    format_integer_value,
    format_metric_value,
    number_or_none,
)


TOTAL_SEGMENT = "Total"
ARM_SWITCHER_CONTAMINATION_THRESHOLD = 0.05
DEFAULT_TOP_ARMS = 10


def _latest_stat_values(stats_rows: pd.DataFrame, segment: str) -> dict[str, dict[int, float]]:
    """Latest cumulative value per (metric, variation) -> {metric: {variation: value}}."""
    if stats_rows is None or stats_rows.empty:
        return {}
    df = stats_rows.copy()
    if "segment" in df.columns:
        df = df[df["segment"] == segment]
    if df.empty:
        return {}
    df["dt"] = pd.to_datetime(df["dt"])
    df = df.sort_values("dt")
    latest = df.groupby(["metric", "variation"], as_index=False).last()

    result: dict[str, dict[int, float]] = {}
    for row in latest.itertuples(index=False):
        value = number_or_none(row.value)
        if value is None:
            continue
        result.setdefault(str(row.metric), {})[int(row.variation)] = value
    return result


def _arm_labels(arms_rows: pd.DataFrame) -> dict[int, str]:
    if arms_rows is None or arms_rows.empty:
        return {}
    return {int(row.variation): str(row.arm) for row in arms_rows.itertuples(index=False)}


def _ordered_arm_variations(
    stat_values: dict[str, dict[int, float]],
    arms_rows: pd.DataFrame,
    labels: dict[int, str],
) -> list[int]:
    participants = stat_values.get("participants", {})
    variations = set(participants)
    if arms_rows is not None and not arms_rows.empty:
        variations.update(int(value) for value in arms_rows["variation"].tolist())
    if not variations:
        return []

    def sort_key(variation: int):
        is_control = 0 if labels.get(variation, "") == "control" else 1
        return (is_control, -float(participants.get(variation, 0)), variation)

    return sorted(variations, key=sort_key)


def _format_window(first_seen: Any, last_seen: Any) -> str:
    first_dt = pd.to_datetime(first_seen, errors="coerce")
    last_dt = pd.to_datetime(last_seen, errors="coerce")
    if pd.isna(first_dt) or pd.isna(last_dt):
        return ""
    return f"{first_dt.strftime('%Y-%m-%d')} — {last_dt.strftime('%Y-%m-%d')}"


def _grouped_window(arms_rows: pd.DataFrame, variations: list[int]) -> str:
    if arms_rows is None or arms_rows.empty or not variations:
        return ""
    subset = arms_rows[arms_rows["variation"].astype(int).isin(variations)]
    if subset.empty:
        return ""
    return _format_window(subset["first_seen_dt"].min(), subset["last_seen_dt"].max())


def _sum_stat(stat_values: dict[str, dict[int, float]], metric: str, variations: list[int]) -> float | None:
    per_variation = stat_values.get(metric)
    if not per_variation:
        return None
    values = [per_variation[variation] for variation in variations if variation in per_variation]
    if not values:
        return None
    return float(sum(values))


def _metric_ratio(
    stat_values: dict[str, dict[int, float]],
    numerator: str,
    denominator: str,
    variations: list[int],
    *,
    percentage: bool,
) -> float | None:
    numerator_value = _sum_stat(stat_values, numerator, variations)
    denominator_value = _sum_stat(stat_values, denominator, variations)
    if numerator_value is None or denominator_value is None or denominator_value == 0:
        return None
    ratio = numerator_value / denominator_value
    return ratio * 100 if percentage else ratio


def build_bandit_arm_confluence_table_code(
    stats_rows: pd.DataFrame,
    arms_rows: pd.DataFrame,
    reconciliation_rows: pd.DataFrame | None = None,
    *,
    metrics_yaml_path: str | Path | None = None,
    segment: str = TOTAL_SEGMENT,
    top_arms: int = DEFAULT_TOP_ARMS,
    thousands_separator: bool = True,
) -> str:
    """Transposed per-arm table: arms as columns, metrics as rows.

    Metric values are recomputed as latest-numerator / latest-denominator from
    ``ug_exp_stats`` counts, which also makes the grouped tail column exact.
    """
    cfg = ExperimentCalculatorConfig.from_env()
    metrics_yaml = metrics_yaml_path or cfg.metrics_yaml_path

    stat_values = _latest_stat_values(stats_rows, segment)
    labels = _arm_labels(arms_rows)
    ordered_variations = _ordered_arm_variations(stat_values, arms_rows, labels)
    if not ordered_variations:
        return _paragraph("No bandit arm data found.")

    top_variations = ordered_variations[:top_arms]
    tail_variations = ordered_variations[top_arms:]

    arm_facts = {}
    if arms_rows is not None and not arms_rows.empty:
        arm_facts = {int(row.variation): row for row in arms_rows.itertuples(index=False)}
    aix_exposures = {}
    if reconciliation_rows is not None and not reconciliation_rows.empty:
        arm_to_variation = {label: variation for variation, label in labels.items()}
        for row in reconciliation_rows.itertuples(index=False):
            variation = arm_to_variation.get(str(row.arm))
            if variation is not None:
                aix_exposures[int(variation)] = int(row.aix_exposures or 0)

    column_groups: list[tuple[str, list[int]]] = [
        (labels.get(variation, f"arm #{variation}"), [variation]) for variation in top_variations
    ]
    if tail_variations:
        column_groups.append((f"other {len(tail_variations)} arms (low traffic / retired early)", tail_variations))

    rows: list[str] = []
    header_cells = [_cell("Arm", background=HEADER_COLOR, bold=True, align="left")]
    header_cells.extend(
        _cell(label, background=HEADER_COLOR, bold=True, align="left") for label, _ in column_groups
    )
    rows.append(_row(header_cells))

    participants_cells = [_row_header_cell("Participants")]
    window_cells = [_row_header_cell("Exposure window")]
    switcher_cells = [_row_header_cell("Arm switcher share")]
    exposures_cells = [_row_header_cell("aix exposures")]
    for _, variations in column_groups:
        participants_value = _sum_stat(stat_values, "participants", variations)
        if participants_value is None and len(variations) == 1 and variations[0] in arm_facts:
            participants_value = float(getattr(arm_facts[variations[0]], "participants", 0) or 0)
        participants_cells.append(
            _cell(format_integer_value(participants_value, thousands_separator=thousands_separator))
        )

        if len(variations) == 1 and variations[0] in arm_facts:
            fact = arm_facts[variations[0]]
            window_cells.append(_cell(_format_window(fact.first_seen_dt, fact.last_seen_dt)))
        else:
            window_cells.append(_cell(_grouped_window(arms_rows, variations)))

        switchers = 0.0
        fact_participants = 0.0
        for variation in variations:
            fact = arm_facts.get(variation)
            if fact is None:
                continue
            switchers += float(getattr(fact, "arm_switchers", 0) or 0)
            fact_participants += float(getattr(fact, "participants", 0) or 0)
        if fact_participants > 0:
            share = switchers / fact_participants
            share_text = format_diff_percent(share * 100, thousands_separator=thousands_separator)
            if share > ARM_SWITCHER_CONTAMINATION_THRESHOLD:
                share_text = f"{share_text} (contaminated)"
            switcher_cells.append(_cell(share_text))
        else:
            switcher_cells.append(_cell(""))

        exposures_total = sum(aix_exposures.get(variation, 0) for variation in variations)
        exposures_cells.append(
            _cell(format_integer_value(exposures_total, thousands_separator=thousands_separator) if exposures_total else "")
        )

    rows.extend([_row(participants_cells), _row(window_cells), _row(switcher_cells), _row(exposures_cells)])

    funnel_configs = _load_metric_table_configs(metrics_yaml, domain="monetization", subdomain="bandit_funnel")
    other_configs = [
        metric_config
        for metric_config in _load_metric_table_configs(metrics_yaml, domain=None, subdomain=None)
        if metric_config not in funnel_configs
    ]

    from .metrics import load_metrics_config, normalize_metric_config

    metrics_config_raw = load_metrics_config(metrics_yaml)

    for metric_config in [*funnel_configs, *other_configs]:
        raw_config = normalize_metric_config(metrics_config_raw.get(metric_config.name, []))
        numerator = str(raw_config.get("numerator") or "")
        denominator = str(raw_config.get("denominator") or "")
        percentage = bool(raw_config.get("percentage", False))
        if not numerator or not denominator:
            continue
        if numerator not in stat_values or denominator not in stat_values:
            continue

        metric_cells = [_row_header_cell(metric_config.display_name)]
        for _, variations in column_groups:
            value = _metric_ratio(stat_values, numerator, denominator, variations, percentage=percentage)
            metric_cells.append(
                _cell(
                    format_metric_value(
                        value,
                        prefix=metric_config.prefix,
                        suffix=metric_config.suffix,
                        thousands_separator=thousands_separator,
                    )
                )
            )
        rows.append(_row(metric_cells))

    return _table(rows, header_row_count=1)


def build_bandit_reconciliation_confluence_table_code(
    reconciliation_rows: pd.DataFrame,
    *,
    thousands_separator: bool = True,
) -> str:
    if reconciliation_rows is None or reconciliation_rows.empty:
        return _paragraph(
            "No recorded aix reconciliation yet — the lifecycle poller counters were "
            "not available at calculation time."
        )

    header = [
        _cell(title, background=HEADER_COLOR, bold=True, align="left")
        for title in (
            "Arm",
            "Participants (warehouse)",
            "OEC users (warehouse)",
            "participants -> OEC, %",
            "aix exposures",
            "aix conversions",
            "aix rate, %",
            "residual, %",
        )
    ]
    rows = [_row(header)]
    for row in reconciliation_rows.itertuples(index=False):
        rows.append(
            _row(
                [
                    _row_header_cell(str(row.arm)),
                    _cell(format_integer_value(row.wh_participants, thousands_separator=thousands_separator)),
                    _cell(format_integer_value(row.wh_oec_users, thousands_separator=thousands_separator)),
                    _cell(format_metric_value(number_or_none(row.wh_rate) * 100 if number_or_none(row.wh_rate) is not None else None, suffix="%", thousands_separator=thousands_separator)),
                    _cell(format_integer_value(row.aix_exposures, thousands_separator=thousands_separator)),
                    _cell(format_integer_value(row.aix_conversions, thousands_separator=thousands_separator)),
                    _cell(format_metric_value(number_or_none(row.aix_rate) * 100 if number_or_none(row.aix_rate) is not None else None, suffix="%", thousands_separator=thousands_separator)),
                    _cell(format_diff_percent(number_or_none(row.residual_pct), thousands_separator=thousands_separator)),
                ]
            )
        )

    recorded_at = pd.to_datetime(reconciliation_rows["recorded_at"].iloc[0], errors="coerce")
    recorded_note = (
        f"Recorded once at {recorded_at.strftime('%Y-%m-%d %H:%M')} UTC on identical date ranges. "
        if not pd.isna(recorded_at)
        else ""
    )
    note = _paragraph(
        escape(
            recorded_note
            + "The residual gap is structural (different dedup, aix counters are cumulative and "
            "unwindowed, arms wait up to 1500 ms for assignment while untagged traffic does not) "
            "and is not re-derived per report. A drift beyond the recorded residual means one of "
            "the two sides changed."
        )
    )
    return _table(rows, header_row_count=1) + "\n" + note


def get_bandit_experiment_confluence_report_code(
    bandit_exp_id: str,
    admin_exp_id: Optional[int] = None,
    *,
    segment: str = TOTAL_SEGMENT,
    top_arms: int = DEFAULT_TOP_ARMS,
    metrics_yaml_path: str | Path | None = None,
    stats_yaml_path: str | Path | None = None,
    config: Optional[ExperimentCalculatorConfig] = None,
    thousands_separator: bool = True,
) -> str:
    """The two-read bandit report.

    Read 1 — the wrapping admin A/B experiment (holdout vs AI bucket): the only
    layer that carries significance; rendered with the ordinary calculator
    tables when ``admin_exp_id`` is given. Read 2 — the per-arm descriptive
    table with p-values suppressed by design, plus the recorded aix
    reconciliation.
    """
    from .bandit import get_bandit_arm_registry, get_bandit_reconciliation, resolve_bandit_admin_experiment
    from .repository import UG_WEB_BANDIT_CLIENT, get_experiment, experiment_output_exp_id

    cfg = config or ExperimentCalculatorConfig.from_env()
    exp_info = get_experiment(bandit_exp_id, config=cfg)
    slug = str(exp_info["aix_experiment_id"])
    output_exp_id = experiment_output_exp_id(exp_info)

    admin_exp_id_resolved = False
    if admin_exp_id is None:
        admin_exp_id = resolve_bandit_admin_experiment(exp_info, config=cfg)
        admin_exp_id_resolved = admin_exp_id is not None

    arms_rows = get_bandit_arm_registry(slug, config=cfg)
    reconciliation_rows = get_bandit_reconciliation(slug, config=cfg)
    stats_rows = get_experiment_stats_confluence_table_data(
        output_exp_id,
        clients=[UG_WEB_BANDIT_CLIENT],
        segments=[segment],
        config=cfg,
    )

    blocks: list[str] = []
    date_start = datetime.datetime.fromtimestamp(int(exp_info["date_start"]), datetime.timezone.utc).date()
    date_end_ts = int(exp_info.get("date_end", 0) or 0)
    date_end_text = (
        datetime.datetime.fromtimestamp(date_end_ts, datetime.timezone.utc).date().isoformat()
        if date_end_ts
        else "running"
    )
    blocks.append(_paragraph(_strong_text("aix experiment: ") + escape(slug)))
    blocks.append(_paragraph(_strong_text("Window: ") + escape(f"{date_start.isoformat()} — {date_end_text}")))
    blocks.append(_paragraph(_strong_text("Cohort entry: ") + escape(str(exp_info.get("experiment_event_start") or ""))))
    if admin_exp_id is not None:
        admin_note = f"#{int(admin_exp_id)}" + (" (auto-resolved)" if admin_exp_id_resolved else "")
        blocks.append(_paragraph(_strong_text("Admin wrapper experiment: ") + escape(admin_note)))

    blocks.append(_heading(2, "Read 1 — Admin A/B holdout (significance)"))
    if admin_exp_id is not None:
        blocks.append(
            _paragraph(
                escape(
                    "Fixed-horizon comparison on the wrapping admin experiment: the holdout "
                    "variation against the AI bucket. This is the only layer where "
                    "significance testing is valid."
                )
            )
        )
        blocks.append(
            get_experiment_confluence_table_code(
                int(admin_exp_id),
                metrics_yaml_path=metrics_yaml_path,
                domain="monetization",
                config=cfg,
                thousands_separator=thousands_separator,
            )
        )
        blocks.append(
            get_experiment_stats_confluence_table_code(
                int(admin_exp_id),
                stats_yaml_path=stats_yaml_path,
                domain="monetization",
                config=cfg,
                thousands_separator=thousands_separator,
            )
        )
    else:
        blocks.append(
            _paragraph(
                escape(
                    "Admin experiment id not provided. Run the ordinary calculator on the "
                    "wrapping admin experiment for the significance read; the per-arm table "
                    "below carries no p-values by design."
                )
            )
        )

    blocks.append(_heading(2, "Read 2 — Bandit arms (descriptive, no p-values by design)"))
    blocks.append(
        _paragraph(
            escape(
                "Arm sizes are endogenous — the bandit gives traffic to winners — so pooled "
                "significance tests over arms are invalid and p-values are suppressed. A young "
                "arm holds a large weight on posterior variance alone: always read rates next "
                "to the arm's participants and exposure window. Post-registration purchase "
                "attribution is a lower bound."
            )
        )
    )
    blocks.append(
        build_bandit_arm_confluence_table_code(
            stats_rows,
            arms_rows,
            reconciliation_rows,
            metrics_yaml_path=metrics_yaml_path,
            segment=segment,
            top_arms=top_arms,
            thousands_separator=thousands_separator,
        )
    )
    blocks.append(
        _ui_expand(
            "aix reconciliation (recorded once)",
            build_bandit_reconciliation_confluence_table_code(
                reconciliation_rows,
                thousands_separator=thousands_separator,
            ),
        )
    )

    return _ui_expand(escape(slug), "\n".join(blocks), expanded=True)
