import string
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from ug_experiment_calculator import crm, metrics, repository
from ug_experiment_calculator.config import ExperimentCalculatorConfig


QUERIES_DIR = Path(repository.__file__).parent / "queries"
CRM_TEMPLATES = [
    "exp_raw_data_crm.sql",
    "crm_email_metrics.sql",
    "crm_experiment_window.sql",
    "get_crm_exps_ids_to_calc.sql",
]


def _crm_exp_info() -> dict:
    return {
        "id": 8018,
        "is_crm": True,
        "provider_experiment_id": "6aa3c93262586c743c3dca16",
        "date_start": 1789139767,
        "date_end": 1789139800,
        "experiment_event_start": crm.CRM_ENTRY_EVENT,
        "attribution_window_seconds": 3 * 86400,
        "clients_list": [crm.UG_CRM_CLIENT],
        "clients_options": "",
        "segments": {"Total": {"pro_rights": "All"}},
    }


def _placeholders(sql: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(sql) if name}


class CrmTemplatesTest(unittest.TestCase):
    def test_templates_are_single_statements(self) -> None:
        # clickhouse_worker rejects any ';', comments included.
        for name in [*CRM_TEMPLATES, "subscriptions_joined_by_sub_date.sql", "monetization_metrics.sql"]:
            self.assertNotIn(";", (QUERIES_DIR / name).read_text(), name)

    def test_monetization_window_is_a_parameter(self) -> None:
        sql = (QUERIES_DIR / "monetization_metrics.sql").read_text()
        self.assertNotIn("604800", sql)
        self.assertIn("attribution_window_seconds", _placeholders(sql))

    def test_subscription_template_takes_source_and_crm_filters(self) -> None:
        sql = (QUERIES_DIR / "subscriptions_joined_by_sub_date.sql").read_text()
        self.assertNotIn("not like '%email%'", sql)
        self.assertTrue({"source_filter_sql", "crm_subscription_sql"} <= _placeholders(sql))


class CrmIdentityTest(unittest.TestCase):
    def test_admin_row_detection(self) -> None:
        self.assertTrue(crm.is_crm_admin_row(1))
        self.assertFalse(crm.is_crm_admin_row(0))
        self.assertFalse(crm.is_crm_admin_row(None))

    def test_crm_client_routes_to_crm_cohort(self) -> None:
        self.assertEqual(repository.exp_raw_data_query_name(crm.UG_CRM_CLIENT, {}), "exp_raw_data_crm")
        self.assertEqual(repository.exp_raw_data_query_name(crm.UG_CRM_CLIENT, {}, insert=True), "exp_raw_data_crm")

    def test_one_launch_and_one_client(self) -> None:
        exp_info = _crm_exp_info()
        with mock.patch.object(repository, "execute_sql", side_effect=AssertionError("no admin history for CRM")):
            launches = repository.get_experiment_launches(exp_info)
            contexts = repository.get_experiment_client_contexts(exp_info)
        self.assertEqual(len(launches), 1)
        self.assertEqual([context["clients_list"] for context in contexts], [[crm.UG_CRM_CLIENT]])


class CrmSourceFiltersTest(unittest.TestCase):
    def test_non_crm_keeps_dropping_email_purchases(self) -> None:
        exp_info = {"id": 7000, "date_start": 1, "date_end": 2}
        self.assertEqual(crm.source_filter_sql(exp_info), crm.DEFAULT_SOURCE_FILTER_SQL)
        self.assertEqual(crm.attribution_window_seconds(exp_info), 604800)

    def test_crm_keeps_every_purchase_and_flags_utm_crm(self) -> None:
        exp_info = _crm_exp_info()
        self.assertEqual(crm.source_filter_sql(exp_info), "1")
        sql = crm.crm_subscription_sql(exp_info)
        self.assertIn("lower(`mua`.`utm_medium`) = 'crm'", sql)
        # the attribution lookup covers the send day plus the 3-day window
        self.assertIn("toDate('2026-09-11') and toDate('2026-09-14')", sql)
        self.assertEqual(crm.attribution_window_seconds(exp_info), 259200)

    def test_build_crm_experiment_segments(self) -> None:
        admin = {"id": 8018, "date_start": 1789139871, "date_end": 1791402528, "provider_experiment_id": "6aa3c93262586c743c3dca16"}
        window = pd.DataFrame(
            {
                "first_event_dt": [pd.Timestamp("2026-09-11 15:16:07")],
                "last_event_dt": [pd.Timestamp("2026-09-11 15:16:39")],
                "users_cnt": [2788420],
                "clicked_utm_campaigns": [["ug_ww_crm_em_web_all_110926_all_om_ss2026_pro_web_free_b"]],
            }
        )
        cfg = ExperimentCalculatorConfig(table_prefix="ug_monetization_sloperator_")
        with mock.patch.object(crm, "execute_sql", return_value=window):
            exp_info = crm.build_crm_experiment(admin, config=cfg)
        self.assertTrue(exp_info["is_crm"])
        self.assertEqual(exp_info["clients_list"], [crm.UG_CRM_CLIENT])
        self.assertEqual(
            list(exp_info["segments"]),
            [crm.CRM_SEGMENT_ALL, crm.CRM_SEGMENT_CRM, crm.CRM_SEGMENT_CAMPAIGN],
        )
        self.assertNotIn("swf", exp_info["segments"][crm.CRM_SEGMENT_ALL])
        self.assertIn("ss2026_pro_web_free_b", exp_info["segments"][crm.CRM_SEGMENT_CAMPAIGN]["swf"])
        # the window is the send itself, not the admin dates
        self.assertEqual(exp_info["date_end"] - exp_info["date_start"], 33)
        # no send_dt column (older query result): the split date falls back to the first start
        self.assertEqual(exp_info["crm_send_ts"], exp_info["date_start"])

    def test_split_date_ignores_a_stray_early_start(self) -> None:
        admin = {"id": 8225, "date_start": 1793000000, "date_end": 1795000000, "provider_experiment_id": "6adfa4e6b2fa2ce6a6884c4f"}
        window = pd.DataFrame(
            {
                "first_event_dt": [pd.Timestamp("2026-10-27 16:31:01")],
                "last_event_dt": [pd.Timestamp("2026-10-28 19:09:30")],
                "send_dt": [pd.Timestamp("2026-10-28 19:03:38")],
                "users_cnt": [2767279],
                "clicked_utm_campaigns": [[]],
            }
        )
        with mock.patch.object(crm, "execute_sql", return_value=window):
            exp_info = crm.build_crm_experiment(admin, config=ExperimentCalculatorConfig())
        # the cohort window still starts at the first start, the report's split date is the mass send
        self.assertEqual(exp_info["date_start"], int(pd.Timestamp("2026-10-27 16:31:01", tz="UTC").timestamp()))
        self.assertEqual(exp_info["crm_send_ts"], int(pd.Timestamp("2026-10-28 19:03:38", tz="UTC").timestamp()))

    def test_admin_segments_are_added_without_rights(self) -> None:
        admin = {
            "id": 8018,
            "date_start": 1789139871,
            "date_end": 1791402528,
            "provider_experiment_id": "6aa3c93262586c743c3dca16",
            "segments": {
                "Total": {"pro_rights": "All", "uwf": "1 = 0"},
                "US": {"pro_rights": "free", "uwf": "bm_country = 'US'"},
            },
        }
        window = pd.DataFrame(
            {
                "first_event_dt": [pd.Timestamp("2026-09-11 15:16:07")],
                "last_event_dt": [pd.Timestamp("2026-09-11 15:16:39")],
                "users_cnt": [2788420],
                "clicked_utm_campaigns": [[]],
            }
        )
        with mock.patch.object(crm, "execute_sql", return_value=window):
            exp_info = crm.build_crm_experiment(admin, config=ExperimentCalculatorConfig())
        # the standard Total wins over the admin one
        self.assertNotIn("uwf", exp_info["segments"]["Total"])
        self.assertEqual(exp_info["segments"]["US"], {"pro_rights": "All", "uwf": "bm_country = 'US'"})
        self.assertEqual(list(exp_info["segments"])[-1], "US")


class CrmMetricsConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        cfg = ExperimentCalculatorConfig()
        self.metrics_config = {
            name: metrics.normalize_metric_config(items)
            for name, items in metrics.load_metrics_config(cfg.metrics_yaml_path).items()
        }

    def _enabled(self, name: str) -> bool:
        return metrics.config_enabled_for_context(self.metrics_config[name], crm.UG_CRM_CLIENT)

    def test_money_and_email_metrics_apply_to_crm(self) -> None:
        for name in ["arpu, $", "delivered -> opened, %", "delivered -> unsubscribed, %"]:
            self.assertTrue(self._enabled(name), name)

    def test_email_metrics_do_not_leak_into_other_clients(self) -> None:
        config = self.metrics_config["delivered -> opened, %"]
        for client in ["UG_WEB", "UGT_IOS", "UG_WEB BANDIT"]:
            self.assertFalse(metrics.config_enabled_for_context(config, client), client)


if __name__ == "__main__":
    unittest.main()
