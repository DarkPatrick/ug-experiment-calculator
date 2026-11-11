import datetime
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
        # the rate names its base and shows both counts: 100 / (500 - 1)
        self.assertEqual(checkout[1], "Did not buy on the paywall")
        self.assertEqual(checkout[4], "100 / 499 = 20.04%")
        self.assertTrue(checkout[7].startswith("\U0001F534"))
        # a count-only step has no base and no rate
        self.assertEqual(cells[1][1], "")
        self.assertEqual(cells[1][4], "")

    def test_short_table(self) -> None:
        cells = _cells(crm_report.build_landing_short_table(crm_report.landing_totals(_steps_frame())))
        self.assertEqual(cells[0][1], "Landing Open")
        self.assertEqual([row[0] for row in cells], ["Variant", "A", "B", "diff, %", "p-value"])


class ReportBlockTest(unittest.TestCase):
    def test_block_order_and_significance_expands(self) -> None:
        exp_info = {"id": 8018, "date_start": 1789139767, "date_end": 1789139800, "variations": 2,
                    "attribution_window_seconds": 259200}
        code = crm_report.build_crm_report_code(
            exp_info,
            email_table="<table>email</table>",
            after_click_table="<table>click</table>",
            after_split_table="<table>split</table>",
            landing_steps_table="<table>steps</table>",
            landing_short_table="<table>short</table>",
            today=datetime.date(2026, 9, 18),
        )
        headings = re.findall(r"<h\d>(.*?)</h\d>", code)
        self.assertEqual(headings, ["Decision", "Next steps", "Significance analysis", "Insights", "Insight summary"])
        expands = re.findall(r'<ac:parameter ac:name="title">(.*?)</ac:parameter>', code)
        self.assertEqual(expands, ["Email campaign", "Landing funnel", "After Click", "After Split"])
        # the per-block descriptions stay, the per-block questions are gone
        self.assertIn("Purchases attributed to a campaign of this send that the recipients clicked.", code)
        self.assertNotIn("Q:", code)
        self.assertIn("Split 2026-09-11, read on day 7 (2026-09-18), attribution window 3 days (to 2026-09-14).", code)
        self.assertIn("A = control (variation 1), B = test (variation 2).", code)

    def test_split_date_is_the_mass_send_not_a_stray_start(self) -> None:
        # one recipient started on 2026-10-27, the send went out on 2026-10-28 19:03
        exp_info = {"id": 8225, "date_start": 1793118661, "crm_send_ts": 1793214180, "attribution_window_seconds": 259200}
        text = crm_report.header_paragraph(exp_info, today=datetime.date(2026, 11, 4))
        self.assertIn("Split 2026-10-28, read on day 7 (2026-11-04), attribution window 3 days (to 2026-10-31).", text)

    def test_interim_read_is_named(self) -> None:
        exp_info = {"id": 8018, "date_start": 1789139767, "date_end": 1789139800, "attribution_window_seconds": 259200}
        self.assertIn("interim read on day 3", crm_report.header_paragraph(exp_info, today=datetime.date(2026, 9, 14)))

    def test_landing_funnel_is_optional(self) -> None:
        exp_info = {"id": 1, "date_start": 1789139767, "date_end": 1789139800}
        code = crm_report.build_crm_report_code(exp_info, email_table="e", after_click_table="c", after_split_table="s")
        self.assertNotIn("Landing funnel", code)


class DispatchTest(unittest.TestCase):
    def test_crm_experiment_gets_the_crm_template(self) -> None:
        cfg = ExperimentCalculatorConfig()
        with mock.patch("ug_experiment_calculator.repository.get_experiment", return_value={"id": 8018, "is_crm": True}), \
             mock.patch("ug_experiment_calculator.crm_report.get_crm_experiment_confluence_report_code", return_value="CRM") as crm_call:
            code = confluence_tables.get_experiment_confluence_report_code(8018, config=cfg, crm_include_landing_funnel=False)
        self.assertEqual(code, "CRM")
        crm_call.assert_called_once_with(8018, include_landing_funnel=False, config=cfg)


if __name__ == "__main__":
    unittest.main()
