import re
import unittest
from pathlib import Path
from unittest import mock

from ug_experiment_calculator import calculator
from ug_experiment_calculator.config import ExperimentCalculatorConfig


QUERIES_DIR = Path(calculator.__file__).parent / "queries"
SEGMENT_TEMPLATES = [
    "exp_raw_data_app.sql",
    "exp_raw_data_app_insert.sql",
    "exp_raw_data_web.sql",
    "exp_raw_data_web_insert.sql",
    "exp_raw_data_mobweb_web_users.sql",
    "exp_raw_data_bandit.sql",
    "subscriptions_joined_by_sub_date.sql",
    "subscriptions_by_sub_date.sql",
]


class SegmentConditionTemplateTest(unittest.TestCase):
    def test_conditions_are_inlined_not_with_aliases(self) -> None:
        # The new ClickHouse analyzer resolves a WITH expression where it is declared,
        # so `funnel_source in (...)` declared on top of a nested query is an unknown identifier.
        for name in SEGMENT_TEMPLATES:
            sql = (QUERIES_DIR / name).read_text()
            self.assertNotIn("where_condition", sql, name)
            self.assertNotIn("having_condition", sql, name)
            self.assertEqual(sql.count("{where_sql}"), 1, name)
            self.assertEqual(sql.count("{having_sql}"), 1, name)

    def test_user_event_filter_runs_on_raw_events(self) -> None:
        for name in SEGMENT_TEMPLATES[:6]:
            sql = (QUERIES_DIR / name).read_text()
            match = re.search(r"from \(.*?`default`\.`ug_rt_events_(?:app|web)`\s*where\s*\(\{where_sql\}\)\s*\) as `ure[aw]`", sql, flags=re.S)
            self.assertIsNotNone(match, name)


class EmptySegmentSlicesTest(unittest.TestCase):
    def test_empty_segment_has_no_slices(self) -> None:
        from ug_experiment_calculator import repository
        import pandas as pd

        cfg = ExperimentCalculatorConfig(table_prefix="ug_monetization_sloperator_")
        with mock.patch.object(repository, "_table_has_column", return_value=True), \
             mock.patch.object(repository, "execute_sql", return_value=pd.DataFrame()):
            slices = repository.create_experiment_users_slice_segments(
                {"id": 8147}, "sandbox.exp_users_8147", "UGT_IOS", "Paid", {"slice": "os"}, "hash", config=cfg
            )
        self.assertEqual(slices, [])


class SegmentFailureIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ExperimentCalculatorConfig(table_prefix="ug_monetization_sloperator_")
        exp_info = {"id": 8147, "date_start": 1, "date_end": 2, "experiment_event_start": "Tour Skip Click",
                    "clients_list": ["UGT_IOS", "UGT_ANDROID"], "segments": {"Total": {}, "Paid": {"uwf": "type = 'paid'"}}}
        contexts = [dict(exp_info, clients_list=[client]) for client in ["UGT_IOS", "UGT_ANDROID"]]
        self.calculated: list[tuple[str, str]] = []
        self.cleanups: list[str] = []

        def create_users(client_exp_info, client, segment_name, segment, config):
            if client == "UGT_IOS" and segment_name == "Paid":
                raise RuntimeError("ILLEGAL_AGGREGATION")
            return "sandbox.ug_monetization_sloperator_exp_users_8147"

        def calc_segment(**kwargs):
            self.calculated.append((kwargs["client"], kwargs["segment_name"]))
            return "temp"

        patches = {
            "get_experiment": mock.Mock(return_value=dict(exp_info)),
            "load_funnels_config": mock.Mock(return_value={}),
            "expand_experiment_clients": mock.Mock(side_effect=lambda info: list(info["clients_list"])),
            "update_trial_conversion_model": mock.Mock(),
            "get_experiment_launches": mock.Mock(return_value=[dict(exp_info)]),
            "get_experiment_client_contexts": mock.Mock(return_value=contexts),
            "get_experiment_users_hash": mock.Mock(return_value="hash"),
            "create_experiment_users_table": mock.Mock(side_effect=create_users),
            "create_experiment_users_slice_segments": mock.Mock(return_value=[]),
            "_calculate_exp_segment_info": mock.Mock(side_effect=calc_segment),
            "cleanup_obsolete_experiment_segments": mock.Mock(side_effect=lambda info, table, client, names, config: self.cleanups.append(f"segments {client}")),
            "cleanup_obsolete_experiment_clients": mock.Mock(side_effect=lambda *a, **k: self.cleanups.append("clients")),
            "update_rollout_split_users_daily": mock.Mock(),
        }
        for name, value in patches.items():
            patcher = mock.patch.object(calculator, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_failed_segment_does_not_stop_the_other_clients(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "1 segment\\(s\\) failed.*UGT_IOS/Paid"):
            calculator.calculate_exp_info(8147, config=self.cfg)
        self.assertEqual(
            self.calculated,
            [("UGT_IOS", "Total"), ("UGT_ANDROID", "Total"), ("UGT_ANDROID", "Paid")],
        )

    def test_no_cleanup_drops_the_failed_segment_or_client(self) -> None:
        with self.assertRaises(RuntimeError):
            calculator.calculate_exp_info(8147, config=self.cfg)
        self.assertEqual(self.cleanups, ["segments UGT_ANDROID"])


if __name__ == "__main__":
    unittest.main()
