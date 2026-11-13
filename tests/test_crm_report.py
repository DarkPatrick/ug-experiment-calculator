import datetime
from pathlib import Path
import re
import unittest
from unittest import mock

import pandas as pd

from ug_experiment_calculator import confluence_tables, crm_report
from ug_experiment_calculator.config import ExperimentCalculatorConfig


def _cells(table: str) -> list[list[str]]:
    rows = re.findall(r"<tr>(.*?)</tr>", table, flags=re.S)
    return [[re.sub(r"<[^>]+>", "", cell) for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, flags=re.S)] for row in rows]


class SignificanceAndDiffTest(unittest.TestCase):
    def test_marks_follow_direction(self) -> None:
        self.assertEqual(crm_report.significance_cell(0.048, 3.0), "\U0001F7E2 0.048")
        self.assertEqual(crm_report.significance_cell(0.01, 0.1, positive=0), "\U0001F534 0.010")
        self.assertEqual(crm_report.significance_cell(0.38, 1.0), "⬜ 0.38")
        self.assertEqual(crm_report.significance_cell(0.0001, -1.0), "\U0001F534 <0.001")
        self.assertEqual(crm_report.significance_cell(None, 1.0), "")

    def test_diff_is_relative_for_rates_and_dollars_for_money(self) -> None:
        self.assertEqual(crm_report.format_diff(crm_report.RATE, 2.20, 2.44), "+10.91%")
        self.assertEqual(crm_report.format_diff(crm_report.MONEY, 2019.4, 2512.3), "+$493")
        self.assertEqual(crm_report.format_diff(crm_report.MONEY_CENTS, 1.46, 1.82), "+$0.36")
        self.assertEqual(crm_report.format_diff(crm_report.COUNT, 88, 100), "")

    def test_money_diff_that_rounds_to_zero_has_no_sign(self) -> None:
        self.assertEqual(crm_report.format_diff(crm_report.MONEY, 23.4, 23.1), "$0")
        self.assertEqual(crm_report.format_diff(crm_report.MONEY, 851.2, 850.9), "$0")
        self.assertEqual(crm_report.format_diff(crm_report.MONEY, 23.4, 22.1), "-$1")

    def test_money_is_whole_dollars_except_per_1000(self) -> None:
        self.assertEqual(crm_report.format_value(crm_report.MONEY, 2512.6), "$2,513")
        self.assertEqual(crm_report.format_value(crm_report.MONEY, 23.4), "$23")
        self.assertEqual(crm_report.format_value(crm_report.MONEY_CENTS, 1.4567), "$1.46")
        self.assertEqual(crm_report.format_value(crm_report.COUNT, 1394415.0), "1,394,415")


class AbTableTest(unittest.TestCase):
    def test_rows_are_a_b_diff_pvalue(self) -> None:
        columns = [
            crm_report.Column("Members", crm_report.COUNT, {1: 1000, 2: 1001}),
            crm_report.Column("ARPPU", crm_report.MONEY, {1: 23.1, 2: 25.9}, {2: 0.02}),
            crm_report.Column("Churn 14d, %", crm_report.RATE, {1: 5.0, 2: 6.0}, {2: 0.3}, positive=0),
        ]
        cells = _cells(crm_report.build_ab_table(columns, [1, 2]))
        self.assertEqual([row[0] for row in cells], ["Variant", "A", "B", "diff, %", "p-value"])
        self.assertEqual(cells[0], ["Variant", "Members", "ARPPU", "Churn 14d, %"])
        self.assertEqual(cells[3], ["diff, %", "", "+$3", "+20.00%"])
        self.assertEqual(cells[4], ["p-value", "", "\U0001F7E2 0.020", "⬜ 0.30"])

    def test_several_test_variations_get_their_own_rows(self) -> None:
        columns = [crm_report.Column("CR, %", crm_report.RATE, {1: 1.0, 2: 1.1, 3: 0.9}, {2: 0.5, 3: 0.5})]
        labels = [row[0] for row in _cells(crm_report.build_ab_table(columns, [1, 2, 3]))]
        self.assertEqual(labels, ["Variant", "A", "B", "C", "diff, % B", "p-value B", "diff, % C", "p-value C"])


def _steps_frame() -> pd.DataFrame:
    base = {
        "opened_paywall": 500, "instant_pay_available": 100, "wallet_tap_paywall": 0, "bought_paywall": 1,
        "bought_paywall_wallet": 0, "went_checkout": 100, "bought_checkout": 25, "bought_checkout_wallet": 10,
        "left_checkout": 75, "left_paywall": 399, "not_bought_main": 474, "saw_emergency": 220,
        "saw_emergency_left_checkout": 40, "saw_emergency_left_paywall": 180, "emergency_closed": 80,
        "wallet_tap_emergency": 0, "emergency_to_checkout": 50, "bought_emergency": 26, "bought_emergency_offer": 0,
        "bought_emergency_checkout": 26, "bought_emergency_left_checkout": 9, "bought_emergency_left_paywall": 17,
        "bought_nothing": 448, "total_bought": 52, "total_bought_wallet": 20,
    }
    test = dict(base, instant_pay_available=400, wallet_tap_paywall=30, bought_paywall=20, bought_paywall_wallet=19,
                went_checkout=60, bought_checkout=15, left_checkout=45, left_paywall=420)
    rows = []
    for variation, values in ((1, base), (2, test)):
        for platform, share in (("desktop", 0.3), ("mobile", 0.7)):
            rows.append({"variation": variation, "platform": platform, **{k: v * share for k, v in values.items()}})
    return pd.DataFrame(rows)


class LandingTablesTest(unittest.TestCase):
    def test_steps_table_layout(self) -> None:
        totals = crm_report.landing_totals(_steps_frame())
        self.assertAlmostEqual(totals[1]["opened_paywall"], 500)
        cells = _cells(crm_report.build_landing_steps_table(totals))
        self.assertEqual(cells[0], ["Step", "% of", "A", "B", "A, %", "B, %", "diff, %", "p-value"])
        labels = [row[0].replace(" ", "").replace("↳ ", "") for row in cells[1:]]
        self.assertEqual(labels[0], "Opened paywall")
        # an optional step that is zero in both variations is dropped (tap on the emergency offer)
        self.assertFalse(any("on the emergency offer" in label for label in labels))
        checkout = next(row for row in cells if "went to checkout" in row[0])
        # the rate names its base; one value per cell: 100 / (500 - 1)
        self.assertEqual(checkout[1], "Did not buy on the paywall")
        self.assertEqual(checkout[4], "20.04%")
        self.assertTrue(checkout[7].startswith("\U0001F534"))
        # every base of a rate is itself a row of the funnel, so its counts are on the page
        numerators = {step.numerator for step in crm_report.LANDING_STEPS}
        denominators = {step.denominator for step in crm_report.LANDING_STEPS if step.denominator}
        self.assertEqual(denominators - numerators, set())
        # a count-only step has no base and no rate
        self.assertEqual(cells[1][1], "")
        self.assertEqual(cells[1][4], "")

    def test_short_table(self) -> None:
        cells = _cells(crm_report.build_landing_short_table(crm_report.landing_totals(_steps_frame())))
        self.assertEqual(cells[0][1], "Landing Open")
        self.assertEqual([row[0] for row in cells], ["Variant", "A", "B", "diff, %", "p-value"])


HEADING_RE = r"<h\d>(.*?)</h\d>"
EXPAND_RE = r'<ac:parameter ac:name="title">(.*?)</ac:parameter>'
FIRST_READ_HEADINGS = ["Decision", "Next steps", "Significance analysis", "Insights", "Insight summary"]
POST_ANALYSIS_HEADINGS = ["Post-analysis", "Post-analysis summary"]
POST_ANALYSIS_NOTE = "After Split follows as a post-analysis once trials mature."
AFTER_SPLIT_DESCRIPTION = "<p>Pure-CRM purchases (utm_medium = crm) of every recipient of the split.</p>"
EXP_A = {"id": 8018, "date_start": 1789139767, "date_end": 1789139800, "variations": 2, "attribution_window_seconds": 259200}
EXP_D = {"id": 8225, "date_start": 1793118661, "crm_send_ts": 1793214180, "attribution_window_seconds": 259200}
CRM_ROW = {"id": 8018, "is_crm": True, "output_exp_id": 9018, "date_start": 1789139767}


def _headings(code: str) -> list[str]:
    return re.findall(HEADING_RE, code)


def _expands(code: str) -> list[str]:
    return re.findall(EXPAND_RE, code)


def _first_read_with_landing() -> str:
    return crm_report.build_crm_report_code(
        EXP_A,
        email_table="<table>email</table>",
        after_click_table="<table>click</table>",
        landing_steps_table="<table>steps</table>",
        landing_short_table="<table>short</table>",
        today=datetime.date(2026, 9, 15),
    )


def _split_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stats = pd.DataFrame([
        {"metric": metric, "variation": variation, "value": value * variation}
        for metric, value in (("crm_participants", 1000), ("subscriptions_cnt", 20), ("charge_cnt", 15),
                              ("revenue", 812.4), ("refund_14d_cnt", 1))
        for variation in (1, 2)
    ])
    results = pd.DataFrame([
        {"metric": "arppu, $", "test_variation": 2, "mean_0": 40.6, "mean_1": 43.1, "pvalue": 0.41},
        {"metric": "members -> subscribers, %", "test_variation": 2, "mean_0": 2.0, "mean_1": 2.4, "pvalue": 0.03},
        {"metric": "charges per user", "test_variation": 2, "mean_0": 0.015, "mean_1": 0.018, "pvalue": 0.2},
        {"metric": "churn 14d, %", "test_variation": 2, "mean_0": 5.0, "mean_1": 6.0, "pvalue": 0.5},
    ])
    extra = pd.DataFrame([
        {"metric": "revenue per 1000 delivered, $", "variation": 1, "value": 0.81, "pvalue": None},
        {"metric": "revenue per 1000 delivered, $", "variation": 2, "value": 1.62, "pvalue": 0.04},
    ])
    return stats, results, extra


def _empty_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    # "missing result rows": the reads return the usual columns and no rows
    return (
        pd.DataFrame(columns=["metric", "variation", "value"]),
        pd.DataFrame(columns=["metric", "test_variation", "mean_0", "mean_1", "pvalue"]),
        pd.DataFrame(columns=["metric", "variation", "value", "pvalue"]),
    )


class FirstReadBlockTest(unittest.TestCase):
    def test_c1_first_read_has_no_after_split_expand(self) -> None:
        code = _first_read_with_landing()
        self.assertEqual(_headings(code), FIRST_READ_HEADINGS)
        self.assertEqual(_expands(code), ["Email campaign", "Landing funnel", "After Click"])
        self.assertIn("<table>click</table>", code)
        self.assertIn("Purchases attributed to a campaign of this send that the recipients clicked.", code)
        self.assertNotIn("Q:", code)
        self.assertNotIn("Pure-CRM purchases (utm_medium = crm) of every recipient of the split.", code)

    def test_c2_landing_funnel_is_optional(self) -> None:
        exp_info = {"id": 1, "date_start": 1789139767, "date_end": 1789139800}
        code = crm_report.build_crm_report_code(exp_info, email_table="e", after_click_table="c", today=datetime.date(2026, 9, 15))
        self.assertEqual(_expands(code), ["Email campaign", "After Click"])
        self.assertNotIn("Landing funnel", code)
        self.assertEqual(_headings(code), FIRST_READ_HEADINGS)

    def test_c3_header_after_window_promises_post_analysis_not_day_7(self) -> None:
        expected = (
            "<p>Split 2026-09-11, read on day 4 (2026-09-15), attribution window 3 days (to 2026-09-14). "
            "After Split follows as a post-analysis once trials mature. Redash experiment id 8018. "
            "A = control (variation 1), B = test (variation 2).</p>"
        )
        text = crm_report.header_paragraph(EXP_A, today=datetime.date(2026, 9, 15))
        self.assertEqual(text, expected)
        self.assertIn(expected, _first_read_with_landing())
        for phrase in ("day 7", "final read", "interim"):
            self.assertNotIn(phrase, text)

    def test_c4_read_before_window_end_is_interim(self) -> None:
        text = crm_report.header_paragraph(EXP_A, today=datetime.date(2026, 9, 13))
        self.assertIn("Split 2026-09-11, interim read on day 2 (2026-09-13) before the attribution window closes, "
                      "attribution window 3 days (to 2026-09-14).", text)
        self.assertIn(POST_ANALYSIS_NOTE, text)
        self.assertNotIn("final read", text)
        self.assertNotIn("day 7", text)

    def test_c5_read_on_window_end_date_is_not_interim(self) -> None:
        text = crm_report.header_paragraph(EXP_A, today=datetime.date(2026, 9, 14))
        self.assertIn("read on day 3 (2026-09-14), attribution window 3 days (to 2026-09-14).", text)
        self.assertNotIn("interim", text)

    def test_c6_split_date_is_the_mass_send_and_day_7_names_no_final_read(self) -> None:
        text = crm_report.header_paragraph(EXP_D, today=datetime.date(2026, 11, 4))
        self.assertIn("Split 2026-10-28, read on day 7 (2026-11-04), attribution window 3 days (to 2026-10-31).", text)
        self.assertIn(POST_ANALYSIS_NOTE, text)
        self.assertNotIn("final read", text)

    def test_e1_after_split_table_keyword_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            crm_report.build_crm_report_code(EXP_A, email_table="e", after_click_table="c", after_split_table="s",
                                             today=datetime.date(2026, 9, 15))

    def test_e2_missing_id_or_date_raises_key_error(self) -> None:
        today = datetime.date(2026, 9, 15)
        for exp_info in ({"date_start": 1789139767}, {"id": 8018}):
            with self.subTest(exp_info=exp_info):
                with self.assertRaises(KeyError):
                    crm_report.header_paragraph(exp_info, today=today)
                with self.assertRaises(KeyError):
                    crm_report.build_crm_after_split_post_analysis_code(
                        exp_info, after_split_table="<table>split</table>", today=today)

    def test_e6_split_date_itself_is_interim_day_0(self) -> None:
        text = crm_report.header_paragraph(EXP_A, today=datetime.date(2026, 9, 11))
        self.assertIn("interim read on day 0 (2026-09-11) before the attribution window closes, "
                      "attribution window 3 days (to 2026-09-14).", text)
        self.assertNotIn("final read", text)

    def test_e7_after_split_occurs_only_in_the_header_note(self) -> None:
        code = _first_read_with_landing()
        self.assertEqual(code.count("After Split"), 1)
        self.assertEqual(code.count(POST_ANALYSIS_NOTE), 1)


class PostAnalysisBlockTest(unittest.TestCase):
    def test_c7_post_analysis_block_layout(self) -> None:
        code = crm_report.build_crm_after_split_post_analysis_code(
            {"id": 8018, "date_start": 1789139767}, after_split_table="<table>split</table>",
            today=datetime.date(2026, 9, 25))
        header = "<p>After Split read on day 14 (2026-09-25). Redash experiment id 8018.</p>"
        self.assertEqual(_headings(code), POST_ANALYSIS_HEADINGS)
        self.assertEqual(_expands(code), ["After Split"])
        for part in (header, AFTER_SPLIT_DESCRIPTION, "<table>split</table>"):
            self.assertEqual(code.count(part), 1, part)
        positions = [code.index(part) for part in ("<h2>Post-analysis</h2>", header, "ui-expand", "<h3>Post-analysis summary</h3>")]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(code.split("<h3>Post-analysis summary</h3>", 1)[1].strip(), "")

    def test_e5_post_analysis_day_counts_from_mass_send(self) -> None:
        code = crm_report.build_crm_after_split_post_analysis_code(
            EXP_D, after_split_table="<table>split</table>", today=datetime.date(2026, 11, 4))
        self.assertIn("<p>After Split read on day 7 (2026-11-04). Redash experiment id 8225.</p>", code)


class PostAnalysisReadTest(unittest.TestCase):
    def _call(self, row, frames, exp_id=8018):
        cfg = ExperimentCalculatorConfig()
        stats, results, extra = frames
        with mock.patch("ug_experiment_calculator.repository.get_experiment", return_value=row), \
             mock.patch("ug_experiment_calculator.crm_report.read_latest_results", return_value=(stats, results)) as read, \
             mock.patch("ug_experiment_calculator.crm_metrics.get_crm_extra_metrics", return_value=extra) as extra_call:
            try:
                code = crm_report.get_crm_after_split_post_analysis_code(exp_id, config=cfg)
            except Exception as error:  # returned so the test can assert on reads made before the error
                code = error
        return cfg, code, read, extra_call

    def test_c8_post_analysis_table_equals_todays_after_split_table(self) -> None:
        stats, results, extra = _split_frames()
        cfg, code, read, extra_call = self._call(CRM_ROW, (stats, results, extra))
        read.assert_called_once_with(9018, crm_report.CRM_SEGMENT_CRM, cfg)
        extra_call.assert_called_once_with(8018, segment=crm_report.CRM_SEGMENT_CRM, config=cfg)
        table = crm_report.build_ab_table(crm_report.money_columns(stats, results, extra, with_clicks=False), [1, 2])
        self.assertEqual(code.count(table), 1)
        self.assertEqual(_headings(code), POST_ANALYSIS_HEADINGS)
        self.assertEqual(_expands(code), ["After Split"])

    def test_c9_non_crm_id_raises_before_any_read(self) -> None:
        row = {"id": 7001, "is_crm": False, "output_exp_id": 7001, "date_start": 1789139767}
        _, error, read, extra_call = self._call(row, _split_frames(), exp_id=7001)
        self.assertIsInstance(error, ValueError)
        self.assertEqual(str(error), "Experiment 7001 is not a CRM experiment")
        read.assert_not_called()
        extra_call.assert_not_called()

    def test_e3_missing_result_rows_render_as_today(self) -> None:
        stats, results, extra = _empty_frames()
        _, code, _, _ = self._call(CRM_ROW, (stats, results, extra))
        table = crm_report.build_ab_table(crm_report.money_columns(stats, results, extra, with_clicks=False), [1, 2])
        self.assertIsInstance(code, str)
        self.assertEqual(_headings(code), POST_ANALYSIS_HEADINGS)
        self.assertEqual(code.count(table), 1)

    def test_e4_falsy_is_crm_variants_raise(self) -> None:
        base = {"id": 7001, "output_exp_id": 7001, "date_start": 1789139767}
        for row in (base, dict(base, is_crm=None), dict(base, is_crm=0)):
            with self.subTest(row=row):
                _, error, read, extra_call = self._call(row, _split_frames(), exp_id=7001)
                self.assertIsInstance(error, ValueError)
                self.assertEqual(str(error), "Experiment 7001 is not a CRM experiment")
                self.assertFalse(read.called or extra_call.called)

    def test_e8_repository_failure_propagates(self) -> None:
        cfg = ExperimentCalculatorConfig()
        failure = RuntimeError("ClickHouse unavailable")
        with mock.patch("ug_experiment_calculator.repository.get_experiment", side_effect=failure), \
             mock.patch("ug_experiment_calculator.crm_report.read_latest_results") as read, \
             mock.patch("ug_experiment_calculator.crm_metrics.get_crm_extra_metrics") as extra_call:
            with self.assertRaises(RuntimeError) as raised:
                crm_report.get_crm_after_split_post_analysis_code(8018, config=cfg)
        self.assertIs(raised.exception, failure)
        self.assertFalse(read.called or extra_call.called)


class FirstReadDataTest(unittest.TestCase):
    def test_c10_first_read_no_longer_reads_after_split_extra_metrics(self) -> None:
        cfg = ExperimentCalculatorConfig()
        stats, results, extra = _split_frames()
        with mock.patch("ug_experiment_calculator.repository.get_experiment", return_value=CRM_ROW), \
             mock.patch("ug_experiment_calculator.crm_report.read_latest_results", return_value=(stats, results)) as read, \
             mock.patch("ug_experiment_calculator.crm_metrics.get_crm_extra_metrics", return_value=extra) as extra_call, \
             mock.patch("ug_experiment_calculator.crm_metrics.get_crm_landing_steps", return_value=pd.DataFrame()):
            code = crm_report.get_crm_experiment_confluence_report_code(8018, include_landing_funnel=False, config=cfg)
        self.assertIn(mock.call(9018, crm_report.CRM_SEGMENT_CRM, cfg), read.call_args_list)
        self.assertNotIn(crm_report.CRM_SEGMENT_CRM, [call.kwargs.get("segment") for call in extra_call.call_args_list])
        self.assertEqual(_expands(code), ["Email campaign", "After Click"])


class DispatchTest(unittest.TestCase):
    def test_c11_dispatch_export_and_version(self) -> None:
        cfg = ExperimentCalculatorConfig()
        with mock.patch("ug_experiment_calculator.repository.get_experiment", return_value={"id": 8018, "is_crm": True}), \
             mock.patch("ug_experiment_calculator.crm_report.get_crm_experiment_confluence_report_code", return_value="CRM") as crm_call:
            code = confluence_tables.get_experiment_confluence_report_code(8018, config=cfg, crm_include_landing_funnel=False)
        self.assertEqual(code, "CRM")
        crm_call.assert_called_once_with(8018, include_landing_funnel=False, config=cfg)
        import ug_experiment_calculator
        from ug_experiment_calculator import get_crm_after_split_post_analysis_code
        self.assertIs(get_crm_after_split_post_analysis_code, crm_report.get_crm_after_split_post_analysis_code)
        self.assertIn("get_crm_after_split_post_analysis_code", ug_experiment_calculator.__all__)
        pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = "0.23.0"', pyproject)


class EmptyFrameTest(unittest.TestCase):
    def test_landing_totals_on_empty_frame_returns_empty(self) -> None:
        self.assertEqual(crm_report.landing_totals(pd.DataFrame()), {})
        self.assertEqual(crm_report.landing_totals(pd.DataFrame(columns=["variation", "platform"])), {})

    def test_stat_on_frame_without_columns_returns_empty(self) -> None:
        self.assertEqual(crm_report._stat(pd.DataFrame(), "members"), {})


class DocsTest(unittest.TestCase):
    def test_c12_readme_describes_first_read_and_post_analysis(self) -> None:
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
        section = readme.split("## CRM-эксперименты (Bloomreach)", 1)[1].split("\n## ", 1)[0]
        block = section.split("**Блок итогов по CRM-шаблону.**", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn("After Split (сегмент `CRM purchases`)", block)
        self.assertIn("get_crm_after_split_post_analysis_code(<id>)", section)
        for part in ("Post-analysis", "Post-analysis summary", "`CRM purchases`", "после 7-го дня"):
            self.assertIn(part, section)


if __name__ == "__main__":
    unittest.main()
