import datetime
import unittest
from unittest import mock

from ug_experiment_calculator import repository
from ug_experiment_calculator.config import ExperimentCalculatorConfig


class UpdateSubscriptionSourceTablesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ExperimentCalculatorConfig.from_env()
        self.calls: list[str] = []
        today = datetime.datetime.now(datetime.timezone.utc).date()

        patches = {
            "_ensure_subscription_source_tables": mock.Mock(return_value=False),
            "_get_table_max_subscribed_date": mock.Mock(return_value=today),
            "_delete_subscriptions_block": mock.Mock(side_effect=lambda table, *a, **k: self.calls.append(f"drop {self._name(table)}")),
            "_sync_table_replicas": mock.Mock(side_effect=lambda table, **k: self.calls.append(f"sync {self._name(table)}")),
            "execute_sql_modify": mock.Mock(side_effect=self._record_insert),
            "get_query": mock.Mock(side_effect=lambda name, params, **k: name),
        }
        for name, value in patches.items():
            patcher = mock.patch.object(repository, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _name(self, table: str) -> str:
        return "transactions" if table == self.cfg.subscription_transactions_table else "subscriptions"

    def _record_insert(self, query: str) -> None:
        table = query.split()[2]
        self.calls.append(f"insert {self._name(table)}")

    def _run(self, *, fresh: bool, mismatches: list[dict]) -> None:
        with mock.patch.object(repository, "_was_subscription_range_updated_recently", return_value=fresh), \
             mock.patch.object(repository, "_subscription_source_mismatches", side_effect=mismatches):
            repository.update_subscription_source_tables(config=self.cfg)

    def test_skips_rebuild_when_range_is_fresh_and_consistent(self) -> None:
        self._run(fresh=True, mismatches=[{}])
        self.assertEqual(self.calls, [])

    def test_rebuilds_fresh_range_when_tables_are_inconsistent(self) -> None:
        self._run(fresh=True, mismatches=[{202610: (26606, 0)}, {}])
        self.assertIn("insert transactions", self.calls)

    def test_transactions_are_built_only_after_subscriptions_reach_all_replicas(self) -> None:
        self._run(fresh=False, mismatches=[{}])
        self.assertEqual(
            self.calls,
            [
                "drop transactions",
                "drop subscriptions",
                "insert subscriptions",
                "sync subscriptions",
                "insert transactions",
                "sync transactions",
            ],
        )

    def test_retries_transactions_build_once_on_mismatch(self) -> None:
        self._run(fresh=False, mismatches=[{202610: (304776, 278300)}, {}])
        self.assertEqual(self.calls.count("insert transactions"), 2)
        self.assertEqual(self.calls.count("insert subscriptions"), 1)

    def test_fails_loudly_when_transactions_stay_inconsistent(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "inconsistent"):
            self._run(fresh=False, mismatches=[{202610: (304776, 278300)}] * 2)


if __name__ == "__main__":
    unittest.main()
