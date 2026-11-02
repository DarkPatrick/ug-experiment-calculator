import unittest
from unittest import mock

from ug_experiment_calculator import calculator, repository
from ug_experiment_calculator.config import ExperimentCalculatorConfig


TEMP_TABLE = "sandbox.ug_monetization_sloperator_exp_subscription_8111_abc"


class SegmentTempTableCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ExperimentCalculatorConfig.from_env()
        patches = {
            "experiment_base_id": mock.Mock(return_value=8111),
            "experiment_output_exp_id": mock.Mock(return_value=8111),
            "experiment_launch_id": mock.Mock(return_value="8111"),
            "create_experiments_subscription_table": mock.Mock(return_value=TEMP_TABLE),
            "get_monetization_metrics": mock.Mock(side_effect=RuntimeError("Memory limit exceeded")),
        }
        for name, value in patches.items():
            patcher = mock.patch.object(calculator, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.drop_table = mock.Mock()
        patcher = mock.patch.object(calculator, "drop_table", self.drop_table)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self) -> None:
        calculator._calculate_exp_segment_info(
            exp_info={},
            funnels_config={},
            exp_users_table="sandbox.exp_users_8111",
            client="UG_IOS",
            segment_name="Total",
            segment={},
            segment_hash="",
            df_tot={},
            df_cum_agg_tot={},
            stats_df_tot={},
            retention_cache={},
            tab_view_cache={},
            product_metrics_segments={},
            config=self.cfg,
        )

    def test_temp_table_is_dropped_when_segment_calculation_fails(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Memory limit"):
            self._run()
        self.drop_table.assert_called_once_with(TEMP_TABLE, config=self.cfg)

    def test_drop_failure_does_not_hide_the_original_error(self) -> None:
        self.drop_table.side_effect = RuntimeError("drop failed")
        with self.assertRaisesRegex(RuntimeError, "Memory limit"):
            self._run()


class CreateTempTableCleanupTest(unittest.TestCase):
    def test_failed_create_as_select_drops_the_partial_table(self) -> None:
        cfg = ExperimentCalculatorConfig.from_env()
        drop_table = mock.Mock()
        with mock.patch.object(repository, "_ensure_subscription_source_tables"), \
             mock.patch.object(repository, "create_transient_table_sql", return_value="create table t"), \
             mock.patch.object(repository, "get_query", return_value="select 1"), \
             mock.patch.object(repository, "execute_sql_modify", side_effect=RuntimeError("Memory limit exceeded")), \
             mock.patch.object(repository, "drop_table", drop_table):
            with self.assertRaisesRegex(RuntimeError, "Memory limit"):
                repository.create_experiments_subscription_table(
                    {"id": 8111, "date_start": 1793000000, "date_end": 0},
                    "UG_IOS",
                    {},
                    config=cfg,
                )
        drop_table.assert_called_once()
        self.assertIn("exp_subscription_", drop_table.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
