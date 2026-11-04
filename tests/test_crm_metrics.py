import math
import string
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from ug_experiment_calculator import crm, crm_metrics, repository
from ug_experiment_calculator.config import ExperimentCalculatorConfig


QUERIES_DIR = Path(repository.__file__).parent / "queries"
TEMPLATES = ["crm_member_ctes.sql", "crm_member_metrics.sql", "crm_payment_mix.sql", "crm_landing_funnel.sql", "crm_landing_steps.sql"]


def _placeholders(sql: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(sql) if name}


class CrmMetricsTemplatesTest(unittest.TestCase):
    def test_single_statement_and_known_parameters(self) -> None:
        allowed = set(crm_metrics._common_params.__code__.co_names) | {
            "exp_users_table", "client_sql", "segment_sql", "segment_hash_sql", "provider_experiment_id_sql",
            "date_start", "date_end", "attribution_window_days", "attribution_window_seconds", "utm_campaigns_sql",
        }
        for name in TEMPLATES:
            sql = (QUERIES_DIR / name).read_text()
            self.assertNotIn(";", sql, name)
            self.assertTrue(_placeholders(sql) <= allowed, (name, _placeholders(sql) - allowed))

    def test_member_ctes_render_on_top_of_monetization_ctes(self) -> None:
        exp_info = {
            "id": 8018,
            "is_crm": True,
            "provider_experiment_id": "6aa3c93262586c743c3dca16",
            "date_start": 1789139767,
            "date_end": 1789139800,
            "attribution_window_seconds": 259200,
            "segments": {"CRM purchases": {"pro_rights": "All", "swf": "1"}},
        }
        cfg = ExperimentCalculatorConfig(table_prefix="ug_monetization_sloperator_")
        ctx = {
            "exp_info": exp_info,
            "segment_name": "CRM purchases",
            "segment": exp_info["segments"]["CRM purchases"],
            "segment_hash": "hash",
            "users_table": "sandbox.exp_users_8018",
            "date_start": pd.Timestamp("2026-09-11", tz="UTC").to_pydatetime(),
            "date_end": pd.Timestamp("2026-09-11", tz="UTC").to_pydatetime(),
        }
        sql = crm_metrics.render_member_ctes_sql(ctx, cfg)
        self.assertTrue(sql.lstrip().startswith("with"))
        self.assertIn("`member_outcomes` as (", sql)
        self.assertIn("`email_flags` as (", sql)
        # the CRM window, not the default 7 days, everywhere in the rendered SQL
        self.assertNotIn("+ 604800", sql)
        self.assertIn("+ 259200", sql)
        self.assertNotIn(";", sql)


class CrmExtraMetricsTest(unittest.TestCase):
    def test_ratio_moments_match_a_direct_delta_method(self) -> None:
        rng = np.random.default_rng(7)
        x = rng.exponential(2.0, 5000) * (rng.random(5000) < 0.1)
        y = (rng.random(5000) < 0.9).astype(float)
        ratio, var = crm_metrics._ratio_moments(len(x), x.sum(), (x * x).sum(), y.sum(), (y * y).sum(), (x * y).sum())
        self.assertAlmostEqual(ratio, x.sum() / y.sum())
        mx, my = x.mean(), y.mean()
        expected = (x.var() - 2 * ratio * np.cov(x, y, bias=True)[0, 1] + ratio**2 * y.var()) / my**2
        self.assertAlmostEqual(var, expected, places=9)

    def test_revenue_per_1000_delivered_and_clicks_to_charges(self) -> None:
        sums = pd.DataFrame(
            [
                {"variation": 1, "members": 1000, "delivered_cnt": 990, "clicked_cnt": 40, "buyer_cnt": 4,
                 "charge_cnt": 4, "revenue": 100.0, "revenue_sq": 2500.0, "revenue_x_delivered": 100.0,
                 "charge_sq": 4, "charge_x_clicked": 3},
                {"variation": 2, "members": 1000, "delivered_cnt": 990, "clicked_cnt": 40, "buyer_cnt": 6,
                 "charge_cnt": 6, "revenue": 150.0, "revenue_sq": 3750.0, "revenue_x_delivered": 150.0,
                 "charge_sq": 6, "charge_x_clicked": 5},
            ]
        )
        result = crm_metrics.build_crm_extra_metrics(sums).set_index(["metric", "variation"])
        revenue = result.loc[("revenue per 1000 delivered, $", 1)]
        self.assertAlmostEqual(revenue["value"], 100.0 / 990 * 1000)
        self.assertTrue(math.isnan(revenue["pvalue"]))
        test = result.loc[("revenue per 1000 delivered, $", 2)]
        self.assertAlmostEqual(test["diff"], 50.0 / 990 * 1000)
        self.assertAlmostEqual(test["lift, %"], 50.0)
        self.assertTrue(0 < test["pvalue"] < 1)
        clicks = result.loc[("clicks -> charges, %", 2)]
        self.assertAlmostEqual(clicks["value"], 6 / 40 * 100)


class CrmLandingFunnelTest(unittest.TestCase):
    def test_all_platform_is_the_sum_and_conversions_use_previous_steps(self) -> None:
        counts = pd.DataFrame(
            [
                {"variation": 1, "platform": "desktop", "landing_open": 200, "instant_pay_available": 60,
                 "emergency_offer_shown": 100, "emergency_to_checkout": 20, "checkout_view": 60,
                 "purchase_click": 15, "access": 30},
                {"variation": 1, "platform": "mobile", "landing_open": 500, "instant_pay_available": 120,
                 "emergency_offer_shown": 150, "emergency_to_checkout": 30, "checkout_view": 120,
                 "purchase_click": 25, "access": 45},
                {"variation": 2, "platform": "desktop", "landing_open": 210, "instant_pay_available": 65,
                 "emergency_offer_shown": 100, "emergency_to_checkout": 20, "checkout_view": 60,
                 "purchase_click": 15, "access": 25},
                {"variation": 2, "platform": "mobile", "landing_open": 510, "instant_pay_available": 440,
                 "emergency_offer_shown": 150, "emergency_to_checkout": 30, "checkout_view": 110,
                 "purchase_click": 25, "access": 60},
            ]
        )
        result = crm_metrics.build_crm_landing_funnel(counts)
        users = result["users"].set_index(["platform", "variation"])
        self.assertEqual(users.loc[("all", 1), "landing_open"], 700)
        self.assertEqual(list(result["users"]["platform"].unique()), ["all", "desktop", "mobile"])
        conversion = result["conversion"].set_index(["platform", "metric", "variation"])
        emergency = conversion.loc[("mobile", "emergency -> checkout, %", 2)]
        self.assertAlmostEqual(emergency["value"], 30 / 150 * 100)
        self.assertEqual(emergency["denominator"], 150)
        self.assertLess(conversion.loc[("mobile", "open -> instant pay, %", 2), "pvalue"], 0.001)

    def test_empty_funnel(self) -> None:
        result = crm_metrics.build_crm_landing_funnel(pd.DataFrame())
        self.assertTrue(result["users"].empty)


class CrmPaymentMixTest(unittest.TestCase):
    def test_shares_and_chi_square(self) -> None:
        counts = pd.DataFrame(
            [
                {"variation": 1, "payment_method": "CreditCard", "charged_subscriptions": 50},
                {"variation": 1, "payment_method": "Apple Pay", "charged_subscriptions": 30},
                {"variation": 2, "payment_method": "CreditCard", "charged_subscriptions": 30},
                {"variation": 2, "payment_method": "Apple Pay", "charged_subscriptions": 50},
            ]
        )
        result = crm_metrics.build_crm_payment_mix(counts)
        control = result[result["variation"] == 1].set_index("payment_method")
        self.assertAlmostEqual(control.loc["CreditCard", "share, %"], 62.5)
        self.assertTrue(control["pvalue"].isna().all())
        test_pvalue = result.loc[result["variation"] == 2, "pvalue"].unique()
        self.assertEqual(len(test_pvalue), 1)
        self.assertLess(test_pvalue[0], 0.05)


class CrmContextTest(unittest.TestCase):
    def test_non_crm_experiment_is_refused(self) -> None:
        with mock.patch.object(repository, "get_experiment", return_value={"id": 7000, "segments": {}}):
            with self.assertRaises(ValueError):
                crm_metrics._crm_context(7000, crm.CRM_SEGMENT_CRM, ExperimentCalculatorConfig())


if __name__ == "__main__":
    unittest.main()
