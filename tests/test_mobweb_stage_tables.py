import datetime
import unittest
from unittest import mock

from ug_experiment_calculator import repository
from ug_experiment_calculator.config import ExperimentCalculatorConfig

SCHEMA = "(\n    `unified_id` Int64,\n    `variation` UInt32\n)"


class EnsureMobwebStageTableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ExperimentCalculatorConfig.from_env()
        self.queries: list[str] = []
        self.dropped: list[str] = []
        patches = {
            "_execute_sql_modify": mock.Mock(side_effect=lambda query, **k: self.queries.append(query)),
            "drop_table": mock.Mock(side_effect=lambda table, **k: self.dropped.append(table)),
            "_wait_for_table_exists": mock.Mock(),
            "_ensure_table_schema_columns": mock.Mock(),
        }
        for name, value in patches.items():
            patcher = mock.patch.object(repository, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _ensure(self, *, exists: bool, engines: set[str]) -> str:
        with mock.patch.object(repository, "_table_exists", return_value=exists), \
             mock.patch.object(repository, "_table_engines", return_value=engines):
            return repository._ensure_mobweb_stage_table(
                "exp_users_7613_mobweb_web_users",
                schema=SCHEMA,
                partition="toDate(`exp_start_dt`, 'UTC')",
                sorting="unified_id",
                config=self.cfg,
            )

    def test_creates_missing_stage_table_as_replicated(self) -> None:
        self._ensure(exists=False, engines=set())
        self.assertEqual(self.dropped, [])
        self.assertEqual(len(self.queries), 1)
        self.assertIn("ReplicatedMergeTree", self.queries[0])

    def test_recreates_stage_table_left_as_plain_merge_tree(self) -> None:
        table = self._ensure(exists=True, engines={"MergeTree"})
        self.assertEqual(self.dropped, [table])
        self.assertEqual(len(self.queries), 1)
        self.assertIn("ReplicatedMergeTree", self.queries[0])

    def test_recreates_stage_table_with_mixed_engines_across_nodes(self) -> None:
        self._ensure(exists=True, engines={"MergeTree", "ReplicatedMergeTree"})
        self.assertEqual(len(self.dropped), 1)

    def test_reuses_replicated_stage_table(self) -> None:
        self._ensure(exists=True, engines={"ReplicatedMergeTree"})
        self.assertEqual(self.dropped, [])
        self.assertEqual(self.queries, [])


class MobwebUsersDayReplicaSyncTest(unittest.TestCase):
    def test_every_stage_reaches_all_replicas_before_the_next_one_reads_it(self) -> None:
        cfg = ExperimentCalculatorConfig.from_env()
        calls: list[str] = []

        def stage_name(table: str) -> str:
            return table.rsplit("_mobweb_", 1)[-1] if "_mobweb_" in table else "exp_users"

        patches = {
            "create_client": mock.Mock(),
            "_ensure_mobweb_stage_table": mock.Mock(side_effect=lambda table_name, **k: cfg.full_table(table_name)),
            "_delete_mobweb_stage_day": mock.Mock(),
            "_insert_into_table_from_select": mock.Mock(side_effect=lambda table, *a, **k: calls.append(f"insert {stage_name(table)}")),
            "_execute_sql_modify": mock.Mock(side_effect=lambda query, **k: calls.append("insert exp_users")),
            "_sync_table_replicas": mock.Mock(side_effect=lambda table, **k: calls.append(f"sync {stage_name(table)}")),
            "get_query": mock.Mock(return_value="select 1"),
            "_experiment_time_params": mock.Mock(return_value={}),
        }
        for name, value in patches.items():
            patcher = mock.patch.object(repository, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        day = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc)
        exp_info = {
            "id": 7613,
            "date_start": int(day.timestamp()),
            "date_end": int((day + datetime.timedelta(days=1)).timestamp()),
        }
        repository._insert_mobweb_experiment_users_day(
            cfg.full_table("exp_users_7613"),
            exp_info,
            "UG_WEB MOBWEB",
            "Total",
            "hash",
            day,
            "1",
            "1",
            config=cfg,
        )

        self.assertEqual(
            calls,
            [
                "insert web_users",
                "sync web_users",
                "insert web_installs",
                "sync web_installs",
                "insert app_users",
                "insert app_users",
                "sync app_users",
                "insert exp_users",
                "sync exp_users",
            ],
        )


class MobwebUsersCacheVersionTest(unittest.TestCase):
    def test_only_mobweb_segments_carry_the_mobweb_cache_version(self) -> None:
        exp_info = {"id": 7613, "date_start": 1, "clients_options": ""}
        with mock.patch.object(repository, "exp_raw_data_query_name", return_value="exp_raw_data_mobweb"):
            mobweb = repository._experiment_users_hash_config(exp_info, "UG_WEB MOBWEB", {})
        with mock.patch.object(repository, "exp_raw_data_query_name", return_value="exp_raw_data_app"):
            app = repository._experiment_users_hash_config(exp_info, "UG_IOS", {})

        self.assertEqual(mobweb["mobweb_cache_version"], repository.MOBWEB_EXPERIMENT_USERS_CACHE_VERSION)
        self.assertNotIn("mobweb_cache_version", app)


if __name__ == "__main__":
    unittest.main()
