import unittest

import numpy as np
import pandas as pd

from ug_experiment_calculator.bandit import (
    BANDIT_ENTRY_EVENT,
    arm_variation_transform_sql,
    assign_arm_variations,
    filter_bandit_experiments,
    is_bandit_experiment_id,
    select_bandit_admin_experiment,
)
from ug_experiment_calculator.bandit_report import build_bandit_arm_confluence_table_code
from ug_experiment_calculator.config import ExperimentCalculatorConfig
from ug_experiment_calculator.metrics import (
    calc_metrics_stats_by_variation_pairs,
    config_enabled_for_context,
    load_metrics_config,
    normalize_metric_config,
    platform_buckets_for_context,
    stats_columns_for_client,
)
from ug_experiment_calculator.repository import (
    UG_WEB_BANDIT_CLIENT,
    UG_WEB_CLIENT,
    base_client_for_calculation,
    exp_raw_data_query_name,
    is_mobweb_segment,
    web_event_platform_filter_sql,
    _experiment_users_query_filters,
)


class BanditIdentityTests(unittest.TestCase):
    def test_slug_is_bandit(self) -> None:
        self.assertTrue(is_bandit_experiment_id("ug_seasons_sale_banner_iter_4"))

    def test_numeric_ids_are_not_bandit(self) -> None:
        self.assertFalse(is_bandit_experiment_id(7832))
        self.assertFalse(is_bandit_experiment_id("7832"))
        self.assertFalse(is_bandit_experiment_id("-7832"))
        self.assertFalse(is_bandit_experiment_id(""))
        self.assertFalse(is_bandit_experiment_id(None))

    def test_bandit_entry_event_in_where_filter(self) -> None:
        exp_info = {
            "id": "ug_test_slug",
            "base_id": -1_000_000_001,
            "is_bandit": True,
            "experiment_event_start": BANDIT_ENTRY_EVENT,
        }
        where_filter, _ = _experiment_users_query_filters(exp_info, {})
        self.assertIn(f"event = '{BANDIT_ENTRY_EVENT}'", where_filter)
        self.assertNotIn("item_id", where_filter)


class BanditDispatchTests(unittest.TestCase):
    def test_bandit_client_maps_to_web_source(self) -> None:
        self.assertEqual(base_client_for_calculation(UG_WEB_BANDIT_CLIENT), UG_WEB_CLIENT)

    def test_bandit_client_dispatches_to_bandit_query(self) -> None:
        self.assertEqual(exp_raw_data_query_name(UG_WEB_BANDIT_CLIENT, {}), "exp_raw_data_bandit")
        self.assertEqual(
            exp_raw_data_query_name(UG_WEB_BANDIT_CLIENT, {}, insert=True),
            "exp_raw_data_bandit",
        )

    def test_bandit_client_is_not_mobweb(self) -> None:
        self.assertFalse(is_mobweb_segment({}, "", UG_WEB_BANDIT_CLIENT))

    def test_bandit_client_has_open_platform_filter(self) -> None:
        self.assertEqual(web_event_platform_filter_sql(UG_WEB_BANDIT_CLIENT, {}), "1")

    def test_bandit_platform_bucket_is_all(self) -> None:
        self.assertEqual(platform_buckets_for_context(UG_WEB_BANDIT_CLIENT), {"all"})


class ArmVariationAssignmentTests(unittest.TestCase):
    def test_control_gets_variation_one(self) -> None:
        discovered = [
            {"arm": "v_pkg_1", "first_seen_dt": "2026-08-20 10:00:00"},
            {"arm": "control", "first_seen_dt": "2026-08-20 10:05:00"},
            {"arm": "v_pkg_2", "first_seen_dt": "2026-08-20 11:00:00"},
        ]
        mapping = assign_arm_variations(discovered, {})
        self.assertEqual(mapping["control"], 1)
        self.assertEqual(mapping["v_pkg_1"], 2)
        self.assertEqual(mapping["v_pkg_2"], 3)

    def test_existing_assignments_never_change(self) -> None:
        discovered = [
            {"arm": "control", "first_seen_dt": "2026-08-20 10:00:00"},
            {"arm": "v_pkg_1", "first_seen_dt": "2026-08-20 10:05:00"},
            {"arm": "v_new", "first_seen_dt": "2026-08-21 09:00:00"},
        ]
        existing = {"control": 1, "v_pkg_1": 2}
        mapping = assign_arm_variations(discovered, existing)
        self.assertEqual(mapping["control"], 1)
        self.assertEqual(mapping["v_pkg_1"], 2)
        self.assertEqual(mapping["v_new"], 3)

    def test_transform_sql_contains_all_arms(self) -> None:
        sql = arm_variation_transform_sql({"control": 1, "v_pkg_5": 2}, "`arm`")
        self.assertIn("transform(`arm`", sql)
        self.assertIn("'control'", sql)
        self.assertIn("'v_pkg_5'", sql)
        self.assertIn("toUInt32(0)", sql)

    def test_empty_mapping_yields_zero(self) -> None:
        self.assertEqual(arm_variation_transform_sql({}, "`arm`"), "toUInt32(0)")


class BanditDiscoveryFilterTests(unittest.TestCase):
    def _experiments_frame(self) -> pd.DataFrame:
        now = pd.Timestamp("2026-08-26", tz="UTC")
        return pd.DataFrame(
            [
                {"aix_experiment_id": "ug_active", "status": "active", "origin": "https://www.ultimate-guitar.com", "created_at": now - pd.Timedelta(days=2), "closed_at": pd.NaT},
                {"aix_experiment_id": "ug_recently_closed", "status": "closed", "origin": "https://www.ultimate-guitar.com", "created_at": now - pd.Timedelta(days=10), "closed_at": now - pd.Timedelta(days=5)},
                {"aix_experiment_id": "ug_old_closed", "status": "closed", "origin": "https://www.ultimate-guitar.com", "created_at": now - pd.Timedelta(days=90), "closed_at": now - pd.Timedelta(days=60)},
                {"aix_experiment_id": "ug_paused_no_closed_at", "status": "paused", "origin": "https://www.ultimate-guitar.com", "created_at": now - pd.Timedelta(days=3), "closed_at": pd.NaT},
                {"aix_experiment_id": "ug_stand", "status": "active", "origin": "https://www.ug.zenkovets.lan", "created_at": now - pd.Timedelta(days=1), "closed_at": pd.NaT},
                {"aix_experiment_id": "musescore_exp", "status": "active", "origin": "https://musescore.com", "created_at": now - pd.Timedelta(days=1), "closed_at": pd.NaT},
            ]
        )

    def test_only_prod_ug_origin_kept(self) -> None:
        result = filter_bandit_experiments(
            self._experiments_frame(),
            now=pd.Timestamp("2026-08-26", tz="UTC"),
        )
        slugs = set(result["aix_experiment_id"])
        self.assertNotIn("ug_stand", slugs)
        self.assertNotIn("musescore_exp", slugs)

    def test_active_plus_recently_ended_semantics(self) -> None:
        result = filter_bandit_experiments(
            self._experiments_frame(),
            include_ended_days=30,
            now=pd.Timestamp("2026-08-26", tz="UTC"),
        )
        slugs = set(result["aix_experiment_id"])
        self.assertEqual(slugs, {"ug_active", "ug_recently_closed", "ug_paused_no_closed_at"})

    def test_created_at_is_the_fallback_for_missing_closed_at(self) -> None:
        result = filter_bandit_experiments(
            self._experiments_frame(),
            include_ended_days=1,
            now=pd.Timestamp("2026-08-26", tz="UTC"),
        )
        slugs = set(result["aix_experiment_id"])
        self.assertEqual(slugs, {"ug_active"})


class AdminWrapperSelectionTests(unittest.TestCase):
    # Real candidate table observed for ug_seasons_sale_banner_iter_4:
    # 7832 is the wrapper (full coverage, holdout depleted), 7808 is an
    # unrelated near-full-coverage experiment with a small variation-1 share.
    ITER_4_CANDIDATES = [
        {"admin_exp_id": 7832, "coverage_share": 1.0, "holdout_share": 0.0, "window_overlap_share": 1.0},
        {"admin_exp_id": 7808, "coverage_share": 0.99876, "holdout_share": 0.004514, "window_overlap_share": 0.02},
        {"admin_exp_id": 7886, "coverage_share": 0.500003, "holdout_share": 0.499237, "window_overlap_share": 1.0},
        {"admin_exp_id": 7910, "coverage_share": 1.0, "holdout_share": 0.4993, "window_overlap_share": 1.0},
    ]

    def test_wrapper_selected_from_real_candidates(self) -> None:
        self.assertEqual(select_bandit_admin_experiment(self.ITER_4_CANDIDATES), 7832)

    def test_no_candidate_when_nothing_passes(self) -> None:
        candidates = [
            {"admin_exp_id": 7910, "coverage_share": 1.0, "holdout_share": 0.4993},
            {"admin_exp_id": 7886, "coverage_share": 0.5, "holdout_share": 0.499},
        ]
        self.assertIsNone(select_bandit_admin_experiment(candidates))

    def test_ambiguity_resolved_by_window_overlap(self) -> None:
        candidates = [
            {"admin_exp_id": 101, "coverage_share": 1.0, "holdout_share": 0.0, "window_overlap_share": 0.1},
            {"admin_exp_id": 102, "coverage_share": 1.0, "holdout_share": 0.0, "window_overlap_share": 0.9},
        ]
        self.assertEqual(select_bandit_admin_experiment(candidates), 102)


class BanditMetricConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ExperimentCalculatorConfig()
        self.metrics_config = load_metrics_config(self.cfg.metrics_yaml_path)

    def test_bandit_funnel_metrics_only_for_bandit_client(self) -> None:
        metric_config = normalize_metric_config(self.metrics_config["participants -> banner click, %"])
        self.assertEqual(metric_config["denominator"], "participants")
        self.assertTrue(config_enabled_for_context(metric_config, UG_WEB_BANDIT_CLIENT))
        self.assertFalse(config_enabled_for_context(metric_config, UG_WEB_CLIENT))
        self.assertFalse(config_enabled_for_context(metric_config, "UGT_IOS"))

    def test_funnel_steps_use_previous_step_denominator(self) -> None:
        expected = {
            "banner view -> banner click, %": ("banner_click_user_cnt", "banner_view_user_cnt"),
            "banner click -> plans view, %": ("plans_view_user_cnt", "banner_click_user_cnt"),
            "plans view -> checkout, %": ("checkout_view_user_cnt", "plans_view_user_cnt"),
            "checkout -> purchase success, %": ("purchase_success_user_cnt", "checkout_view_user_cnt"),
        }
        for metric_name, (numerator, denominator) in expected.items():
            metric_config = normalize_metric_config(self.metrics_config[metric_name])
            self.assertEqual(metric_config["numerator"], numerator, metric_name)
            self.assertEqual(metric_config["denominator"], denominator, metric_name)

    def test_existing_metrics_apply_to_bandit_client_unchanged(self) -> None:
        for metric_name in ("arpu, $", "trial -> charge, %", "web retention 7d, %"):
            metric_config = normalize_metric_config(self.metrics_config[metric_name])
            self.assertTrue(
                config_enabled_for_context(metric_config, UG_WEB_BANDIT_CLIENT),
                metric_name,
            )

    def test_bandit_stats_columns_selected_for_bandit_client_only(self) -> None:
        bandit_columns = stats_columns_for_client(self.cfg.stats_yaml_path, UG_WEB_BANDIT_CLIENT)
        self.assertIn("participants", bandit_columns)
        self.assertIn("banner_click_user_cnt", bandit_columns)
        self.assertIn("members", bandit_columns)

        web_columns = stats_columns_for_client(self.cfg.stats_yaml_path, UG_WEB_CLIENT)
        self.assertNotIn("participants", web_columns)
        self.assertNotIn("banner_click_user_cnt", web_columns)


class SuppressSignificanceTests(unittest.TestCase):
    def _cumulative_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"dt": "2026-08-20", "variation": 1, "participants": 1000, "banner_click_user_cnt": 30},
                {"dt": "2026-08-20", "variation": 2, "participants": 2000, "banner_click_user_cnt": 90},
            ]
        )

    def test_pvalues_suppressed_for_bandit(self) -> None:
        cfg = ExperimentCalculatorConfig()
        stats_df = calc_metrics_stats_by_variation_pairs(
            cumulative_df=self._cumulative_frame(),
            metrics_yaml_path=cfg.metrics_yaml_path,
            control_variation=1,
            client=UG_WEB_BANDIT_CLIENT,
            suppress_significance=True,
        )
        metric_rows = stats_df[stats_df["metric"] == "participants -> banner click, %"]
        self.assertEqual(len(metric_rows), 1)
        row = metric_rows.iloc[0]
        self.assertTrue(np.isnan(row["pvalue"]))
        self.assertTrue(np.isnan(row["ci_low"]))
        self.assertTrue(np.isnan(row["ci_high"]))
        self.assertAlmostEqual(row["mean_0"], 3.0)
        self.assertAlmostEqual(row["mean_1"], 4.5)
        self.assertAlmostEqual(row["lift"], 50.0)

    def test_pvalues_present_without_suppression(self) -> None:
        cfg = ExperimentCalculatorConfig()
        stats_df = calc_metrics_stats_by_variation_pairs(
            cumulative_df=self._cumulative_frame(),
            metrics_yaml_path=cfg.metrics_yaml_path,
            control_variation=1,
            client=UG_WEB_BANDIT_CLIENT,
            suppress_significance=False,
        )
        metric_rows = stats_df[stats_df["metric"] == "participants -> banner click, %"]
        self.assertEqual(len(metric_rows), 1)
        self.assertFalse(np.isnan(metric_rows.iloc[0]["pvalue"]))


class BanditArmTableTests(unittest.TestCase):
    def _stats_rows(self, arms_cnt: int) -> pd.DataFrame:
        rows = []
        for variation in range(1, arms_cnt + 1):
            participants = 1000 * (arms_cnt - variation + 1)
            rows.append({"dt": "2026-08-25", "metric": "participants", "variation": variation, "value": participants, "client": UG_WEB_BANDIT_CLIENT, "segment": "Total"})
            rows.append({"dt": "2026-08-25", "metric": "banner_view_user_cnt", "variation": variation, "value": participants, "client": UG_WEB_BANDIT_CLIENT, "segment": "Total"})
            rows.append({"dt": "2026-08-25", "metric": "banner_click_user_cnt", "variation": variation, "value": participants // 100, "client": UG_WEB_BANDIT_CLIENT, "segment": "Total"})
        return pd.DataFrame(rows)

    def _arms_rows(self, arms_cnt: int) -> pd.DataFrame:
        rows = []
        for variation in range(1, arms_cnt + 1):
            rows.append(
                {
                    "arm": "control" if variation == 1 else f"v_pkg_{variation}",
                    "variation": variation,
                    "first_seen_dt": "2026-08-20 10:00:00",
                    "last_seen_dt": "2026-08-25 10:00:00",
                    "participants": 1000 * (arms_cnt - variation + 1),
                    "arm_switchers": 10,
                }
            )
        return pd.DataFrame(rows)

    def test_large_pool_grouped_beyond_top_arms(self) -> None:
        arms_cnt = 20
        table_code = build_bandit_arm_confluence_table_code(
            self._stats_rows(arms_cnt),
            self._arms_rows(arms_cnt),
            None,
            top_arms=10,
        )
        self.assertIn("other 10 arms", table_code)
        self.assertIn("control", table_code)
        self.assertIn("Participants", table_code)
        self.assertIn("Exposure window", table_code)
        self.assertIn("Arm switcher share", table_code)
        # p-values never appear in the per-arm table
        self.assertNotIn("pvalue", table_code)

    def test_control_arm_is_first_column(self) -> None:
        table_code = build_bandit_arm_confluence_table_code(
            self._stats_rows(3),
            self._arms_rows(3),
            None,
            top_arms=10,
        )
        control_pos = table_code.find(">control<")
        other_pos = table_code.find(">v_pkg_2<")
        self.assertGreater(control_pos, -1)
        self.assertGreater(other_pos, control_pos)


if __name__ == "__main__":
    unittest.main()
