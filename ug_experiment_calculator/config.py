from __future__ import annotations

from dataclasses import dataclass
import datetime
import os
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_QUERIES_DIR = PACKAGE_DIR / "queries"
DEFAULT_METRICS_YAML_PATH = PACKAGE_DIR / "metrics.yaml"
DEFAULT_STATS_YAML_PATH = PACKAGE_DIR / "stats.yaml"
DEFAULT_FUNNELS_YAML_PATH = PACKAGE_DIR / "funnels.yaml"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ExperimentCalculatorConfig:
    database: str = "sandbox"
    cluster: str = "ug_core"
    table_prefix: str = ""
    subscriptions_start_date: datetime.date = datetime.date(2011, 6, 1)
    queries_dir: Path = DEFAULT_QUERIES_DIR
    metrics_yaml_path: Path = DEFAULT_METRICS_YAML_PATH
    stats_yaml_path: Path = DEFAULT_STATS_YAML_PATH
    funnels_yaml_path: Path = DEFAULT_FUNNELS_YAML_PATH
    default_clients: tuple[str, ...] = ("UGT_IOS", "UGT_ANDROID", "UG_WEB")
    update_subscription_sources: bool = False

    @classmethod
    def from_env(cls, prefix: str = "EXPERIMENT_") -> "ExperimentCalculatorConfig":
        start_date = os.environ.get(f"{prefix}SUBSCRIPTIONS_START_DATE", "2011-06-01")
        queries_dir = os.environ.get(f"{prefix}QUERIES_DIR")
        metrics_yaml_path = os.environ.get(f"{prefix}METRICS_YAML_PATH")
        stats_yaml_path = os.environ.get(f"{prefix}STATS_YAML_PATH")
        funnels_yaml_path = os.environ.get(f"{prefix}FUNNELS_YAML_PATH")
        default_clients = tuple(
            client.strip()
            for client in os.environ.get(f"{prefix}DEFAULT_CLIENTS", "UGT_IOS,UGT_ANDROID,UG_WEB").split(",")
            if client.strip()
        )

        return cls(
            database=os.environ.get(f"{prefix}CH_DATABASE", "sandbox"),
            cluster=os.environ.get(f"{prefix}CH_CLUSTER", "ug_core"),
            table_prefix=os.environ.get(f"{prefix}CH_TABLE_PREFIX", ""),
            subscriptions_start_date=datetime.datetime.strptime(start_date, "%Y-%m-%d").date(),
            queries_dir=Path(queries_dir) if queries_dir else DEFAULT_QUERIES_DIR,
            metrics_yaml_path=Path(metrics_yaml_path) if metrics_yaml_path else DEFAULT_METRICS_YAML_PATH,
            stats_yaml_path=Path(stats_yaml_path) if stats_yaml_path else DEFAULT_STATS_YAML_PATH,
            funnels_yaml_path=Path(funnels_yaml_path) if funnels_yaml_path else DEFAULT_FUNNELS_YAML_PATH,
            default_clients=default_clients,
            update_subscription_sources=_env_bool(f"{prefix}UPDATE_SUBSCRIPTION_SOURCES", False),
        )

    def physical_table(self, logical_table_name: str) -> str:
        if "." in logical_table_name:
            _, table_name = logical_table_name.split(".", 1)
        else:
            table_name = logical_table_name

        if self.table_prefix and not table_name.startswith(self.table_prefix):
            return f"{self.table_prefix}{table_name}"
        return table_name

    def full_table(self, logical_table_name: str) -> str:
        if "." in logical_table_name:
            database, table_name = logical_table_name.split(".", 1)
            return f"{database}.{self.physical_table(table_name)}"
        return f"{self.database}.{self.physical_table(logical_table_name)}"

    def zookeeper_path(self, logical_table_name: str) -> str:
        physical_table = self.physical_table(logical_table_name)
        return f"/service/clickhouse/{self.cluster}/tables/{{shard}}/{self.database}/{physical_table}"

    @property
    def subscriptions_table(self) -> str:
        return self.full_table("subscriptions")

    @property
    def subscription_transactions_table(self) -> str:
        return self.full_table("subscriptions_transactions")

    @property
    def exp_results_table(self) -> str:
        return self.full_table("ug_exp_results")

    @property
    def exp_stats_table(self) -> str:
        return self.full_table("ug_exp_stats")

    @property
    def exp_funnel_stats_table(self) -> str:
        return self.full_table("ug_exp_funnel_stats")

    @property
    def exp_funnel_results_table(self) -> str:
        return self.full_table("ug_exp_funnel_results")
