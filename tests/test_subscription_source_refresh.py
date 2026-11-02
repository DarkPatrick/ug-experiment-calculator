import datetime
import unittest
from typing import Optional
from unittest import mock

import pandas as pd

from ug_experiment_calculator import repository
from ug_experiment_calculator.config import ExperimentCalculatorConfig


STAGING = {"subscriptions": "stg_subscriptions", "subscriptions_transactions": "stg_transactions"}


class UpdateSubscriptionSourceTablesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ExperimentCalculatorConfig.from_env()
        self.calls: list[str] = []
        self.mismatches: list[dict] = []
        self.live_rows_missing = False
        self.staged_rows_missing = False
        self.failing_insert = ""
        today = datetime.datetime.now(datetime.timezone.utc).date()

        patches = {
            "_ensure_subscription_source_tables": mock.Mock(return_value=False),
            "_get_table_max_subscribed_date": mock.Mock(return_value=today),
            "_create_subscription_staging_tables": mock.Mock(side_effect=self._create_staging),
            "_truncate_table": mock.Mock(side_effect=lambda table, **k: self.calls.append(f"truncate {self._name(table)}")),
            "_sync_table_replicas": mock.Mock(side_effect=lambda table, **k: self.calls.append(f"sync {self._name(table)}")),
            "_replace_partition": mock.Mock(side_effect=lambda target, ym, source, **k: self.calls.append(f"replace {self._name(target)} from {self._name(source)}")),
            "_subscription_source_rows_by_month": mock.Mock(side_effect=self._rows_by_month),
            "_subscription_source_mismatches": mock.Mock(side_effect=lambda *a, **k: self.mismatches.pop(0) if self.mismatches else {}),
            "_subscribed_event_months": mock.Mock(side_effect=lambda start, end: set(repository._iter_year_months(start, end))),
            "drop_table": mock.Mock(side_effect=lambda table, **k: self.calls.append(f"drop table {self._name(table)}")),
            "execute_sql_modify": mock.Mock(side_effect=self._record_insert),
            "get_query": mock.Mock(side_effect=lambda name, params, **k: name),
        }
        for name, value in patches.items():
            patcher = mock.patch.object(repository, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _create_staging(self, **kwargs) -> dict[str, str]:
        self.calls.append("create staging")
        return dict(STAGING)

    def _name(self, table: str) -> str:
        return {
            self.cfg.subscriptions_table: "subscriptions",
            self.cfg.subscription_transactions_table: "transactions",
            STAGING["subscriptions"]: "staged subscriptions",
            STAGING["subscriptions_transactions"]: "staged transactions",
        }[table]

    def _rows_by_month(self, table: str, date_start: datetime.date, date_end: datetime.date) -> dict[int, int]:
        is_staged = table in STAGING.values()
        if (is_staged and self.staged_rows_missing) or (not is_staged and self.live_rows_missing):
            return {}
        return {year_month: 100 for year_month in repository._iter_year_months(date_start, date_end)}

    def _record_insert(self, query: str) -> None:
        table = query.split()[2]
        if self._name(table) == self.failing_insert:
            raise RuntimeError("Memory limit (for query) exceeded")
        self.calls.append(f"insert {self._name(table)}")

    def _run(self, *, fresh: bool) -> None:
        with mock.patch.object(repository, "_was_subscription_range_updated_recently", return_value=fresh):
            repository.update_subscription_source_tables(config=self.cfg)

    def _replaces(self) -> list[str]:
        return [call for call in self.calls if call.startswith("replace")]

    def test_skips_rebuild_when_range_is_fresh_consistent_and_complete(self) -> None:
        self._run(fresh=True)
        self.assertEqual(self.calls, [])

    def test_rebuilds_fresh_range_when_tables_are_inconsistent(self) -> None:
        self.mismatches = [{202610: (26606, 0)}]
        self._run(fresh=True)
        self.assertIn("insert staged transactions", self.calls)

    def test_rebuilds_fresh_range_when_a_finished_month_is_missing(self) -> None:
        self.live_rows_missing = True
        self._run(fresh=True)
        self.assertIn("insert staged subscriptions", self.calls)

    def test_block_is_built_in_staging_and_swapped_in(self) -> None:
        self._run(fresh=False)
        self.assertEqual(self.calls[0], "create staging")
        self.assertEqual(
            self.calls[1:7],
            [
                "truncate staged subscriptions",
                "truncate staged transactions",
                "insert staged subscriptions",
                "sync staged subscriptions",
                "insert staged transactions",
                "sync staged transactions",
            ],
        )
        swaps = self.calls[7:7 + len(self._replaces())]
        self.assertTrue(swaps)
        self.assertEqual(
            swaps,
            ["replace subscriptions from staged subscriptions", "replace transactions from staged transactions"] * (len(swaps) // 2),
        )
        self.assertEqual(self.calls[7 + len(swaps):9 + len(swaps)], ["sync subscriptions", "sync transactions"])
        self.assertNotIn("insert subscriptions", self.calls)
        self.assertNotIn("insert transactions", self.calls)
        self.assertEqual(self.calls[-2:], ["drop table staged subscriptions", "drop table staged transactions"])

    def test_every_built_month_is_swapped_in(self) -> None:
        self._run(fresh=False)
        today = datetime.datetime.now(datetime.timezone.utc).date()
        date_start, date_end = repository._month_date_range(
            today - datetime.timedelta(days=repository.SUBSCRIPTION_SOURCE_INCREMENTAL_LOOKBACK_DAYS),
            today,
        )
        months = len(list(repository._iter_year_months(date_start, date_end)))
        self.assertEqual(len(self._replaces()), 2 * months)

    def test_retries_transactions_build_once_on_mismatch(self) -> None:
        self.mismatches = [{202610: (304776, 278300)}, {}]
        self._run(fresh=False)
        self.assertEqual(self.calls.count("insert staged transactions"), self.calls.count("insert staged subscriptions") + 1)

    def test_fails_loudly_when_transactions_stay_inconsistent(self) -> None:
        self.mismatches = [{202610: (304776, 278300)}] * 2
        with self.assertRaisesRegex(RuntimeError, "inconsistent"):
            self._run(fresh=False)
        self.assertEqual(self._replaces(), [])
        self.assertIn("drop table staged subscriptions", self.calls)

    def test_failed_insert_leaves_live_partitions_untouched(self) -> None:
        self.failing_insert = "staged subscriptions"
        with self.assertRaisesRegex(RuntimeError, "Memory limit"):
            self._run(fresh=False)
        self.assertEqual(self._replaces(), [])
        self.assertEqual(self.calls[-2:], ["drop table staged subscriptions", "drop table staged transactions"])

    def test_empty_build_is_not_swapped_in(self) -> None:
        self.staged_rows_missing = True
        with self.assertRaisesRegex(RuntimeError, "produced no rows"):
            self._run(fresh=False)
        self.assertEqual(self._replaces(), [])


class EnsureSubscriptionSourceCoversTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ExperimentCalculatorConfig.from_env()
        self.today = datetime.datetime.now(datetime.timezone.utc).date()

    def _check(self, rows: dict[int, int], date_start: datetime.date, event_months: Optional[set[int]] = None) -> None:
        if event_months is None:
            event_months = set(repository._iter_year_months(date_start, self.today))
        with mock.patch.object(repository, "_subscription_source_rows_by_month", return_value=rows), \
             mock.patch.object(repository, "_subscribed_event_months", return_value=event_months):
            repository.ensure_subscription_source_covers(date_start, self.today, config=self.cfg)

    def _previous_month_start(self) -> datetime.date:
        return (self.today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)

    def test_raises_on_a_missing_finished_month(self) -> None:
        rows = {self.today.year * 100 + self.today.month: 10}
        with self.assertRaisesRegex(RuntimeError, "no subscriptions"):
            self._check(rows, self._previous_month_start())

    def test_a_month_without_subscribed_events_is_not_a_gap(self) -> None:
        current_month = self.today.year * 100 + self.today.month
        self._check({current_month: 10}, self._previous_month_start(), event_months={current_month})

    def test_an_empty_current_month_is_allowed(self) -> None:
        self._check({}, self.today.replace(day=1))


class EnsureTrialColumnTypeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ExperimentCalculatorConfig.from_env()

    def _ensure(self, column_type: str) -> tuple[bool, mock.Mock]:
        modify = mock.Mock()
        with mock.patch.object(repository, "execute_sql", return_value=pd.DataFrame({"column_type": [column_type]})), \
             mock.patch.object(repository, "execute_sql_modify", modify):
            changed = repository._ensure_trial_column_type(self.cfg.subscriptions_table, config=self.cfg)
        return changed, modify

    def test_uint8_trial_is_widened_and_forces_a_full_refresh(self) -> None:
        changed, modify = self._ensure("UInt8")
        self.assertTrue(changed)
        self.assertIn("modify column `trial` UInt16", modify.call_args.args[0])

    def test_uint16_trial_is_left_alone(self) -> None:
        changed, modify = self._ensure("UInt16")
        self.assertFalse(changed)
        modify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
