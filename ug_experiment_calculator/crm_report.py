"""Confluence итоги block of a CRM (Bloomreach) experiment, in the layout of the CRM page template.

The CRM team's template (Confluence "UG AI Analytics & CRM", page 852003068) differs from the
monetization report: no Forecast / Design vs Reality / per-client expands, the decision first, then
Email campaign, Landing funnel (a step-by-step funnel plus a short version), After Click and After
Split tables, then Insights. Every A/B table has the rows A, B, ``diff, %`` and ``p-value`` with
the metrics in columns (the landing funnel has steps in rows), diffs are relative except money
(in $), absolute money and ARPPU are whole dollars, revenue per 1000 delivered keeps two decimals,
and a tested cell is marked 🟢 significantly better / 🔴 significantly worse / ⬜ not significant.

``get_experiment_confluence_report_code`` dispatches here for every CRM experiment, so the agent
gets this layout without asking for it. The text the analyst owns — the Q&A line of each block,
Decision, Next steps, Insights — is left empty, as in the monetization report.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from html import escape
import logging
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from clickhouse_worker import clickhouse_string_literal as _clickhouse_string_literal
from clickhouse_worker import execute_sql
import pandas as pd
from scipy import stats as scipy_stats

from .config import ExperimentCalculatorConfig
from .crm import (
    CRM_ATTRIBUTION_WINDOW_DAYS,
    CRM_SEGMENT_CAMPAIGN,
    CRM_SEGMENT_CRM,
    UG_CRM_CLIENT,
    attribution_window_seconds,
)
from .value_formatting import (
    format_diff_percent,
    format_integer_value,
    format_metric_value,
    format_pvalue,
    number_or_none,
)


logger = logging.getLogger(__name__)

CONTROL_VARIATION = 1
ALPHA = 0.05
SIGNIFICANT_BETTER = "\U0001F7E2"
SIGNIFICANT_WORSE = "\U0001F534"
NOT_SIGNIFICANT = "⬜"
STEP_ARROW = "↳ "
STEP_INDENT = "  "

# kinds of a column: how its values, diff and p-value are rendered
COUNT = "count"            # whole number, no diff, no test
RATE = "rate"              # share in %, relative diff, tested
MONEY = "money"            # whole dollars, diff in whole $, tested when a p-value is given
MONEY_CENTS = "money_cents"  # dollars with cents (revenue per 1000 delivered), diff in $


@dataclass
class Column:
    label: str
    kind: str
    values: dict[int, Any]
    pvalues: dict[int, Any] = field(default_factory=dict)
    positive: int = 1


def variant_label(variation: int) -> str:
    """Variation 1 is the control A, the test variations are B, C, …"""
    return chr(ord("A") + int(variation) - 1)


def significance_cell(pvalue: Any, diff: Any, positive: int = 1) -> str:
    p_number = number_or_none(pvalue)
    if p_number is None:
        return ""
    text = "<0.001" if p_number < 0.001 else format_pvalue(p_number)
    diff_number = number_or_none(diff)
    if p_number >= ALPHA or diff_number is None or diff_number == 0:
        return f"{NOT_SIGNIFICANT} {text}"
    better = (diff_number > 0) == (int(positive) == 1)
    return f"{SIGNIFICANT_BETTER if better else SIGNIFICANT_WORSE} {text}"


def format_value(kind: str, value: Any) -> str:
    if number_or_none(value) is None:
        return ""
    if kind == COUNT:
        return format_integer_value(value)
    if kind == RATE:
        return format_metric_value(value, suffix="%")
    if kind == MONEY:
        return "$" + format_integer_value(value) if float(value) >= 0 else "-$" + format_integer_value(-float(value))
    if kind == MONEY_CENTS:
        return format_metric_value(value, prefix="$")
    return format_metric_value(value)


def format_diff(kind: str, control: Any, test: Any) -> str:
    """Relative change for rates, change in $ for money, nothing for counts."""
    control_number, test_number = number_or_none(control), number_or_none(test)
    if control_number is None or test_number is None or kind == COUNT:
        return ""
    delta = test_number - control_number
    if kind in (MONEY, MONEY_CENTS):
        magnitude = format_value(kind, abs(delta))
        if not any(ch in "123456789" for ch in magnitude):
            return magnitude  # rounds to zero: "$0" / "$0.00", never "-$0"
        sign = "+" if delta > 0 else "-"
        return f"{sign}{magnitude}"
    if control_number == 0:
        return ""
    lift = (test_number / control_number - 1) * 100
    return ("+" if lift > 0 else "") + format_diff_percent(lift)


def _difference(kind: str, control: Any, test: Any) -> float | None:
    control_number, test_number = number_or_none(control), number_or_none(test)
    if control_number is None or test_number is None:
        return None
    return test_number - control_number


# --- storage helpers (the same markup as confluence_tables) --------------------------------------

def _cell(value: str, *, header: bool = False, align: str = "right") -> str:
    tag = "th" if header else "td"
    body = f"<strong>{escape(value)}</strong>" if header else escape(value)
    return f'<{tag} style="text-align:{align}"><p>{body}</p></{tag}>'


def _row(cells: Sequence[str]) -> str:
    return "<tr>" + "".join(cells) + "</tr>"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]], *, first_column_align: str = "left") -> str:
    lines = ["<table>", "  <thead>", "    " + _row([_cell(text, header=True, align="left" if i == 0 else "right") for i, text in enumerate(header)]), "  </thead>", "  <tbody>"]
    for row in rows:
        lines.append("    " + _row([_cell(text, align=first_column_align if i == 0 else "right") for i, text in enumerate(row)]))
    lines.extend(["  </tbody>", "</table>"])
    return "\n".join(lines)


def _heading(level: int, text: str) -> str:
    return f"<h{level}>{escape(text)}</h{level}>"


def _ui_expand(title: str, body: str) -> str:
    """A collapsed UI Expand, as the per-client blocks of a product experiment's Significance analysis."""
    return "\n".join([
        f'<ac:structured-macro ac:name="ui-expand" ac:macro-id="{uuid.uuid4()}">',
        f'  <ac:parameter ac:name="title">{escape(title)}</ac:parameter>',
        "  <ac:rich-text-body>",
        body,
        "  </ac:rich-text-body>",
        "</ac:structured-macro>",
    ])


def _paragraph(text: str) -> str:
    return f"<p>{escape(text)}</p>"


# --- tables ----------------------------------------------------------------------------------------

def build_ab_table(columns: Sequence[Column], variations: Sequence[int]) -> str:
    """A / B / diff, % / p-value rows, metrics in columns (the CRM table rule)."""
    variations = sorted(int(v) for v in variations)
    tests = [v for v in variations if v != CONTROL_VARIATION]
    header = ["Variant", *[column.label for column in columns]]
    rows = [[variant_label(v), *[format_value(c.kind, c.values.get(v)) for c in columns]] for v in variations]
    for test in tests:
        suffix = "" if len(tests) == 1 else f" {variant_label(test)}"
        rows.append([f"diff, %{suffix}", *[format_diff(c.kind, c.values.get(CONTROL_VARIATION), c.values.get(test)) for c in columns]])
        rows.append([
            f"p-value{suffix}",
            *[
                significance_cell(c.pvalues.get(test), _difference(c.kind, c.values.get(CONTROL_VARIATION), c.values.get(test)), c.positive)
                for c in columns
            ],
        ])
    return _table(header, rows)


@dataclass
class FunnelStep:
    label: str
    numerator: str
    denominator: Optional[str] = None
    level: int = 0
    positive: int = 1
    optional: bool = False


# The step-by-step funnel of the CRM template: at each step who bought there, the rest go on.
LANDING_STEPS = [
    FunnelStep("Opened paywall", "opened_paywall"),
    FunnelStep("Apple Pay / Google Pay button shown (Instant Pay Available)", "instant_pay_available", "opened_paywall", 1, optional=True),
    FunnelStep("Tapped Apple Pay / Google Pay on the paywall", "wallet_tap_paywall", "opened_paywall", 1, optional=True),
    FunnelStep("Bought right on the paywall (button on the paywall, no checkout)", "bought_paywall", "opened_paywall", 1),
    FunnelStep("of them with Apple Pay / Google Pay", "bought_paywall_wallet", "wallet_tap_paywall", 2, optional=True),
    # count-only row: the base of the checkout rate, so its denominator is on the page (one value per cell)
    FunnelStep("Did not buy on the paywall", "not_bought_paywall", None, 1),
    FunnelStep("Did not buy on the paywall, went to checkout", "went_checkout", "not_bought_paywall", 1),
    FunnelStep("Bought on checkout", "bought_checkout", "went_checkout", 2),
    FunnelStep("of them with Apple Pay / Google Pay on checkout", "bought_checkout_wallet", None, 3),
    FunnelStep("Left checkout", "left_checkout", "went_checkout", 2, positive=0),
    FunnelStep("Left the paywall without buying or reaching checkout", "left_paywall", None, 1),
    FunnelStep("Did not buy the main offer (left checkout + left paywall)", "not_bought_main"),
    FunnelStep("Saw the emergency offer", "saw_emergency", "not_bought_main", 1),
    FunnelStep("of those who left checkout", "saw_emergency_left_checkout", "left_checkout", 2),
    FunnelStep("of those who left the paywall", "saw_emergency_left_paywall", "left_paywall", 2),
    FunnelStep("Closed the emergency offer", "emergency_closed", "saw_emergency", 2, positive=0),
    FunnelStep("Tapped Apple Pay / Google Pay on the emergency offer", "wallet_tap_emergency", "saw_emergency", 2, optional=True),
    FunnelStep("Went to checkout from it", "emergency_to_checkout", "saw_emergency", 2),
    FunnelStep("Bought from the emergency offer", "bought_emergency", "saw_emergency", 2),
    FunnelStep("right on the offer (button, no checkout)", "bought_emergency_offer", None, 3),
    FunnelStep("on checkout", "bought_emergency_checkout", None, 3),
    FunnelStep("of those who left checkout", "bought_emergency_left_checkout", "saw_emergency_left_checkout", 3),
    FunnelStep("of those who left the paywall", "bought_emergency_left_paywall", "saw_emergency_left_paywall", 3),
    FunnelStep("Bought nothing", "bought_nothing", "opened_paywall", positive=0),
    FunnelStep("Total bought (paywall + checkout + emergency)", "total_bought", "opened_paywall"),
    FunnelStep("of them paid with Apple Pay / Google Pay", "total_bought_wallet", "total_bought", 1),
]

# What each step's rate is a share of: shown in the "% of" column next to the rates.
STEP_BASES = {
    "opened_paywall": "Opened paywall",
    "wallet_tap_paywall": "Tapped Apple Pay / Google Pay on the paywall",
    "not_bought_paywall": "Did not buy on the paywall",
    "went_checkout": "Went to checkout",
    "not_bought_main": "Did not buy the main offer",
    "left_checkout": "Left checkout",
    "left_paywall": "Left the paywall",
    "saw_emergency": "Saw the emergency offer",
    "saw_emergency_left_checkout": "Saw the emergency offer after leaving checkout",
    "saw_emergency_left_paywall": "Saw the emergency offer after leaving the paywall",
    "total_bought": "Total bought",
}


def _proportion_pvalue(x0: float, n0: float, x1: float, n1: float) -> float | None:
    if n0 <= 0 or n1 <= 0:
        return None
    pooled = (x0 + x1) / (n0 + n1)
    variance = pooled * (1 - pooled) * (1 / n0 + 1 / n1)
    if variance <= 0:
        return 1.0 if x0 / n0 == x1 / n1 else 0.0
    z = (x1 / n1 - x0 / n0) / variance ** 0.5
    return float(2 * scipy_stats.norm.sf(abs(z)))


def landing_totals(steps: pd.DataFrame, platform: str = "all") -> dict[int, dict[str, float]]:
    """Per-variation counts of crm_landing_steps.sql for one device (or all), plus derived steps."""
    frame = steps if platform == "all" else steps[steps["platform"] == platform]
    totals: dict[int, dict[str, float]] = {}
    for variation, group in frame.groupby("variation"):
        values = {key: float(group[key].sum()) for key in group.columns if key not in ("variation", "platform")}
        values["not_bought_paywall"] = values["opened_paywall"] - values["bought_paywall"]
        totals[int(variation)] = values
    return totals


def build_landing_steps_table(totals: Mapping[int, Mapping[str, float]]) -> str:
    """Steps in rows; columns Step, % of, A, B, A %, B %, diff, %, p-value (the CRM funnel exception).

    One value per cell: the A / B columns hold the step's counts, the "% of" column names the base
    step (whose own counts are on its row), and the rate columns hold only the rate.
    """
    variations = sorted(totals)
    if CONTROL_VARIATION not in totals or len(variations) < 2:
        return ""
    test = next(v for v in variations if v != CONTROL_VARIATION)
    control_values, test_values = totals[CONTROL_VARIATION], totals[test]
    header = [
        "Step",
        "% of",
        variant_label(CONTROL_VARIATION),
        variant_label(test),
        f"{variant_label(CONTROL_VARIATION)}, %",
        f"{variant_label(test)}, %",
        "diff, %",
        "p-value",
    ]
    rows = []
    for step in LANDING_STEPS:
        x0, x1 = control_values.get(step.numerator, 0.0), test_values.get(step.numerator, 0.0)
        if step.optional and x0 == 0 and x1 == 0:
            continue
        label = (STEP_INDENT * max(step.level - 1, 0) + STEP_ARROW if step.level else "") + step.label
        row = [label, "", format_integer_value(x0), format_integer_value(x1), "", "", "", ""]
        if step.denominator:
            n0, n1 = control_values.get(step.denominator, 0.0), test_values.get(step.denominator, 0.0)
            r0 = x0 / n0 * 100 if n0 > 0 else None
            r1 = x1 / n1 * 100 if n1 > 0 else None
            row[1] = STEP_BASES.get(step.denominator, step.denominator)
            row[4] = format_value(RATE, r0)
            row[5] = format_value(RATE, r1)
            if r0 is not None and r1 is not None:
                row[6] = format_diff(RATE, r0, r1) if r0 else ""
                row[7] = significance_cell(_proportion_pvalue(x0, n0, x1, n1), r1 - r0, step.positive)
        rows.append(row)
    return _table(header, rows)


def build_landing_short_table(totals: Mapping[int, Mapping[str, float]]) -> str:
    """The quick view under the funnel, in the standard A / B / diff / p-value layout."""
    variations = sorted(totals)
    rates = {
        "paywall": ("bought_paywall", "opened_paywall"),
        "checkout": ("bought_checkout", "opened_paywall"),
        "main": ("bought_main", "opened_paywall"),
        "emergency": ("bought_emergency", "saw_emergency"),
        "total": ("total_bought", "opened_paywall"),
    }
    values = {v: dict(totals[v]) for v in variations}
    for v in variations:
        values[v]["bought_main"] = values[v]["bought_paywall"] + values[v]["bought_checkout"]

    def rate_column(label: str, key: str) -> Column:
        numerator, denominator = rates[key]
        column = Column(label, RATE, {}, {})
        for v in variations:
            n = values[v][denominator]
            column.values[v] = values[v][numerator] / n * 100 if n > 0 else None
        control = values.get(CONTROL_VARIATION)
        for v in variations:
            if v != CONTROL_VARIATION and control is not None:
                column.pvalues[v] = _proportion_pvalue(
                    control[numerator], control[denominator], values[v][numerator], values[v][denominator]
                )
        return column

    def count_column(label: str, key: str) -> Column:
        return Column(label, COUNT, {v: values[v][key] for v in variations})

    columns = [
        count_column("Landing Open", "opened_paywall"),
        count_column("Bought right on paywall", "bought_paywall"),
        rate_column("Landing Open → Bought on paywall, %", "paywall"),
        count_column("Bought on checkout", "bought_checkout"),
        rate_column("Landing Open → Bought on checkout, %", "checkout"),
        rate_column("Landing Open → Bought, main offer, %", "main"),
        count_column("Saw emergency offer", "saw_emergency"),
        count_column("Bought from emergency offer", "bought_emergency"),
        rate_column("Emergency offer → Bought, %", "emergency"),
        count_column("Total bought", "total_bought"),
        rate_column("Landing Open → Total bought, %", "total"),
    ]
    return build_ab_table(columns, variations)


# --- data ------------------------------------------------------------------------------------------

def read_latest_results(exp_id: int, segment: str, config: ExperimentCalculatorConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Latest cumulative stats (metric, variation, value) and pairwise results (metric,
    test_variation, mean_0, mean_1, pvalue) of one CRM segment."""
    def filters(alias: str) -> str:
        return (
            f"`{alias}`.`exp_id` = {int(exp_id)} and `{alias}`.`client` = {_clickhouse_string_literal(UG_CRM_CLIENT)} "
            f"and `{alias}`.`segment` = {_clickhouse_string_literal(segment)}"
        )

    stats = execute_sql(
        f"""
        select
            `ues`.`metric` as `metric`,
            `ues`.`variation` as `variation`,
            argMax(`ues`.`value`, toDate(`ues`.`dt`)) as `value`
        from
            {config.exp_stats_table} as `ues`
        where
            {filters("ues")}
        group by
            `metric`,
            `variation`
        """
    )
    results = execute_sql(
        f"""
        select
            `uer`.`metric` as `metric`,
            `uer`.`test_variation` as `test_variation`,
            argMax(`uer`.`mean_0`, toDate(`uer`.`dt`)) as `mean_0`,
            argMax(`uer`.`mean_1`, toDate(`uer`.`dt`)) as `mean_1`,
            argMax(`uer`.`pvalue`, toDate(`uer`.`dt`)) as `pvalue`
        from
            {config.exp_results_table} as `uer`
        where
            {filters("uer")}
        group by
            `metric`,
            `test_variation`
        """
    )
    return stats, results


def _stat(stats: pd.DataFrame, metric: str) -> dict[int, float]:
    rows = stats[stats["metric"] == metric]
    return {int(row.variation): float(row.value) for row in rows.itertuples()}


def _metric(results: pd.DataFrame, metric: str, scale: float = 1.0) -> tuple[dict[int, float], dict[int, float]]:
    """Per-variation value (control from mean_0) and per-test p-value of a calculator metric."""
    rows = results[results["metric"] == metric]
    values: dict[int, float] = {}
    pvalues: dict[int, float] = {}
    for row in rows.itertuples():
        values[CONTROL_VARIATION] = float(row.mean_0) * scale
        values[int(row.test_variation)] = float(row.mean_1) * scale
        pvalues[int(row.test_variation)] = row.pvalue
    return values, pvalues


def _metric_column(results: pd.DataFrame, label: str, metric: str, kind: str, *, scale: float = 1.0, positive: int = 1) -> Column:
    values, pvalues = _metric(results, metric, scale)
    return Column(label, kind, values, pvalues, positive)


def _extra_column(extra: pd.DataFrame, label: str, metric: str, kind: str) -> Column:
    rows = extra[extra["metric"] == metric]
    return Column(
        label,
        kind,
        {int(row.variation): float(row.value) for row in rows.itertuples()},
        {int(row.variation): row.pvalue for row in rows.itertuples() if int(row.variation) != CONTROL_VARIATION},
    )


def email_columns(stats: pd.DataFrame, results: pd.DataFrame) -> list[Column]:
    return [
        Column("Members", COUNT, _stat(stats, "crm_participants")),
        Column("Sent", COUNT, _stat(stats, "email_sent_user_cnt")),
        Column("Delivered", COUNT, _stat(stats, "email_delivered_user_cnt")),
        _metric_column(results, "DR, %", "sent -> delivered, %", RATE),
        Column("Opened", COUNT, _stat(stats, "email_opened_user_cnt")),
        _metric_column(results, "OR, %", "delivered -> opened, %", RATE),
        Column("Clicked", COUNT, _stat(stats, "email_clicked_user_cnt")),
        _metric_column(results, "CTR, %", "opened -> clicked, %", RATE),
        _metric_column(results, "CR, %", "delivered -> clicked, %", RATE),
        Column("Unsubscribed", COUNT, _stat(stats, "email_unsubscribed_user_cnt")),
        _metric_column(results, "Unsubscribed, %", "delivered -> unsubscribed, %", RATE, positive=0),
    ]


def money_columns(stats: pd.DataFrame, results: pd.DataFrame, extra: pd.DataFrame, *, with_clicks: bool) -> list[Column]:
    columns = [
        Column("Members", COUNT, _stat(stats, "crm_participants")),
        Column("Subscriptions", COUNT, _stat(stats, "subscriptions_cnt")),
        Column("Charges", COUNT, _stat(stats, "charge_cnt")),
        Column("Revenue", MONEY, _stat(stats, "revenue")),
        _metric_column(results, "ARPPU", "arppu, $", MONEY),
        _metric_column(results, "Members → Subs, %", "members -> subscribers, %", RATE),
        _metric_column(results, "Members → Charges, %", "charges per user", RATE, scale=100),
    ]
    if with_clicks:
        columns.append(_extra_column(extra, "Clicks → Charges, %", "clicks -> charges, %", RATE))
    columns.extend([
        _extra_column(extra, "Revenue per 1000 delivered", "revenue per 1000 delivered, $", MONEY_CENTS),
        Column("Refunds 14d", COUNT, _stat(stats, "refund_14d_cnt")),
        _metric_column(results, "Churn 14d, %", "churn 14d, %", RATE, positive=0),
    ])
    return columns


# --- the block ------------------------------------------------------------------------------------

def header_paragraph(exp_info: Mapping[str, Any], *, today: Optional[datetime.date] = None) -> str:
    split_ts = int(exp_info.get("crm_send_ts") or exp_info["date_start"])
    split = datetime.datetime.fromtimestamp(split_ts, datetime.timezone.utc).date()
    window_end = split + datetime.timedelta(seconds=attribution_window_seconds(dict(exp_info)))
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    day = (today - split).days
    read = f"read on day {day} ({today:%Y-%m-%d})" if day >= 7 else f"interim read on day {day} ({today:%Y-%m-%d}), the final read is on day 7"
    variations = int(exp_info.get("variations") or 2)
    labels = ", ".join(
        f"{variant_label(v)} = {'control' if v == CONTROL_VARIATION else 'test'} (variation {v})" for v in range(1, variations + 1)
    )
    return _paragraph(
        f"Split {split:%Y-%m-%d}, {read}, attribution window {CRM_ATTRIBUTION_WINDOW_DAYS} days "
        f"(to {window_end:%Y-%m-%d}). Redash experiment id {int(exp_info['id'])}. {labels}."
    )


def build_crm_report_code(
    exp_info: Mapping[str, Any],
    *,
    email_table: str,
    after_click_table: str,
    after_split_table: str,
    landing_steps_table: str = "",
    landing_short_table: str = "",
    today: Optional[datetime.date] = None,
) -> str:
    """Assemble the Results block from already rendered tables (pure, for tests and previews)."""
    blocks = [
        header_paragraph(exp_info, today=today),
        _heading(2, "Decision"),
        _heading(3, "Next steps"),
        # The standard read, like Significance analysis of a product experiment: tables and what
        # each one counts, no per-block questions (they are the same in every project).
        _heading(2, "Significance analysis"),
        _ui_expand("Email campaign", "\n".join([
            _paragraph(
                "Unique recipients of the campaign with each email event within the attribution window "
                "after their AB Test Start."
            ),
            email_table,
        ])),
    ]
    if landing_steps_table:
        blocks.append(_ui_expand("Landing funnel", "\n".join(block for block in [
            _paragraph(
                "Members who opened the CRM landing from a campaign of this send within the window. "
                "Step by step: at each step we count who bought there, and only those who did not buy "
                "go on. Each rate is a share of the step named in the \u201c% of\u201d column. Where a purchase happened = the step of the last Landing "
                "Checkout View before it (paywall = a payment button on the paywall itself, otherwise "
                "checkout). A purchase before the first Landing Ambulance View counts for the main "
                "offer, after it for the emergency offer. Bought = web PURCHASE_SUCCESS. Apple Pay / "
                "Google Pay = the payment method of the first charge."
            ),
            landing_steps_table,
            _paragraph("Short version (quick view)") if landing_short_table else "",
            landing_short_table,
        ] if block)))
    blocks.extend([
        _ui_expand("After Click", "\n".join([
            _paragraph("Purchases attributed to a campaign of this send that the recipients clicked."),
            after_click_table,
        ])),
        _ui_expand("After Split", "\n".join([
            _paragraph("Pure-CRM purchases (utm_medium = crm) of every recipient of the split."),
            after_split_table,
        ])),
        _heading(2, "Insights"),
        _heading(3, "Insight summary"),
    ])
    return "\n".join(block for block in blocks if block)


def get_crm_experiment_confluence_report_code(
    exp_id: int,
    *,
    include_landing_funnel: bool = True,
    config: Optional[ExperimentCalculatorConfig] = None,
) -> str:
    """The CRM Results block for a calculated CRM experiment.

    ``include_landing_funnel=False`` drops the Landing funnel for a test that does not touch the
    paywall or checkout (an email-only test shows the same funnel in every variation).
    """
    from .crm_metrics import get_crm_extra_metrics, get_crm_landing_steps
    from .repository import get_experiment

    cfg = config or ExperimentCalculatorConfig.from_env()
    exp_info = get_experiment(exp_id, config=cfg)
    output_exp_id = int(exp_info.get("output_exp_id", exp_info["id"]))

    split_stats, split_results = read_latest_results(output_exp_id, CRM_SEGMENT_CRM, cfg)
    click_stats, click_results = read_latest_results(output_exp_id, CRM_SEGMENT_CAMPAIGN, cfg)
    variations = sorted(int(v) for v in split_stats["variation"].unique()) if not split_stats.empty else [1, 2]

    split_extra = get_crm_extra_metrics(exp_id, segment=CRM_SEGMENT_CRM, config=cfg)
    click_extra = get_crm_extra_metrics(exp_id, segment=CRM_SEGMENT_CAMPAIGN, config=cfg)

    landing_steps_table = landing_short_table = ""
    if include_landing_funnel:
        totals = landing_totals(get_crm_landing_steps(exp_id, config=cfg))
        landing_steps_table = build_landing_steps_table(totals)
        landing_short_table = build_landing_short_table(totals) if landing_steps_table else ""

    return build_crm_report_code(
        exp_info,
        email_table=build_ab_table(email_columns(split_stats, split_results), variations),
        after_click_table=build_ab_table(money_columns(click_stats, click_results, click_extra, with_clicks=True), variations),
        after_split_table=build_ab_table(money_columns(split_stats, split_results, split_extra, with_clicks=False), variations),
        landing_steps_table=landing_steps_table,
        landing_short_table=landing_short_table,
    )
