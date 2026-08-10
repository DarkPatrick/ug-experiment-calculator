# Technical Guide for AI Agents

This file is the operational contract for coding agents working in this repository. It complements `README.md` with implementation-level invariants, method preconditions, and common failure modes.

## Repository Map

- `ug_experiment_calculator/calculator.py` orchestrates full experiment calculation. Prefer changing this file only when the end-to-end pipeline changes.
- `ug_experiment_calculator/repository.py` owns ClickHouse I/O, transient/source/result table management, experiment metadata, and SQL template execution.
- `ug_experiment_calculator/metrics.py` owns cumulative aggregation, pairwise statistics, YAML normalization, metric/stat filtering, and funnel math.
- `ug_experiment_calculator/config.py` owns environment parsing and physical ClickHouse table names.
- `ug_experiment_calculator/rollout.py` owns rollout share and rollout impact estimates.
- `ug_experiment_calculator/confluence_tables.py`, `confluence_charts.py`, `echarts.py`, `summary_tables.py`, and `value_formatting.py` are presentation/output layers.
- SQL templates live in `ug_experiment_calculator/queries/`.
- Metric, stat, and funnel definitions live in `metrics.yaml`, `stats.yaml`, and `funnels.yaml`.
- Public lazy exports are listed in `ug_experiment_calculator/__init__.py`.

## Preferred Entry Points

Use `calculate_exp_info(exp_id, config=None, update_rollout=True)` for a normal experiment recalculation. It handles experiment metadata, launch windows, users, subscription temp tables, monetization, product metrics, funnels, result writes, cleanup, and rollout updates.

Use lower-level repository methods only when you intentionally need part of the pipeline. Many of them require intermediate tables with specific schemas.

For Confluence/report output, use getters that read ClickHouse when possible:

- `get_experiment_confluence_report_code(...)`
- `get_experiment_confluence_table_code(...)`
- `get_experiment_stats_confluence_table_code(...)`
- `get_latest_experiment_summary_tables(...)`
- `get_metric_confluence_chart_code(...)`
- `get_metric_confluence_lift_chart_code(...)`
- `get_metric_echarts_code(...)`

Use `build_*` variants only when you already have rows/DataFrames locally.

## Critical Table Contracts

There are three different subscription table shapes. Do not confuse them.

1. `cfg.subscriptions_table`, normally `sandbox.subscriptions`
   - Built by `subscriptions_store_by_sub_date.sql`.
   - Partitioned by `toYYYYMM(toDate(subscribed_dt))`.
   - Contains subscription attributes such as `subscription_id`, `product_code`, `subscribed_dt`, `next_subscribed_dt`, `trial`, `funnel_source`, `product_id`, `unified_id`, `payment_account_id`, `service_name`, `is_otp`, and `is_access_intro`.
   - Does not contain `charge_dt`, `cancel_dt`, `refund_dt`, `upgrade_dt`, revenue columns, or lifetime charge arrays.

2. `cfg.subscription_transactions_table`, normally `sandbox.subscriptions_transactions`
   - Built by `subscription_transactions_store_by_sub_date.sql`.
   - Also keyed by subscription and `subscribed_dt`.
   - Contains transaction-derived fields such as `charge_dt`, `cancel_dt`, `refund_dt`, `upgrade_dt`, `revenue_gross`, `refund_revenue_gross`, `upgrade_revenue`, `all_charges_arr`, and `all_charges_arr_uniq`.

3. `exp_subscription_{exp_launch_id}_{session_id}`
   - Created by `create_experiments_subscription_table(...)` using `subscriptions_joined_by_sub_date.sql`.
   - This is the joined experiment-local subscription table.
   - It contains both subscription attributes and transaction fields, including `charge_dt`.
   - Pass this table to `get_monetization_metrics(...)` and funnel helpers that need subscription events.

If ClickHouse reports `no column 'sta.charge_dt' in table 'sta'` inside `monetization_metrics.sql`, the usual cause is that `get_monetization_metrics(...)` was called with raw `subscriptions` instead of the joined `exp_subscription_*` table.

## Full Calculation Flow

The normal `calculate_exp_info` flow is:

1. Load `ExperimentCalculatorConfig` from the explicit `config` or `.env`/environment.
2. Load experiment metadata with `get_experiment(exp_id)`.
3. Expand clients, including `UG_WEB` into `UG_WEB DESKTOP` and/or `UG_WEB MOBWEB` when the experiment config requires it.
4. If `cfg.update_subscription_sources` is true, call `update_subscription_source_tables(...)`.
5. Always update `trial_conversion_model` via `update_trial_conversion_model(...)`.
6. Split the experiment into launch windows from history.
7. For each launch/client/segment, create or refresh `exp_users_{exp_launch_id}`.
8. Materialize slice-derived segments from the base users cache when a segment has `slice`.
9. Create `exp_subscription_{exp_launch_id}_{session_id}` via `create_experiments_subscription_table(...)`.
10. Run `get_monetization_metrics(...)` against that joined temp table.
11. Run retention and tab-view product metrics against `exp_users`.
12. Run enabled funnels.
13. Calculate cumulative metric/stat/funnel values and pairwise `1 vs N` results.
14. Drop only the target result partitions and insert fresh rows.
15. Drop the temporary subscription table.
16. Update rollout tables for the latest launch when `update_rollout=True`.

## Source Subscription Refresh

`update_subscription_source_tables(...)` maintains `subscriptions` and `subscriptions_transactions`.

- Missing source tables are created from `cfg.subscriptions_start_date` for one day, then updated.
- Existing tables are checked for required columns and `source_version`.
- `SUBSCRIPTION_SOURCE_VERSION` controls forced refresh when source SQL semantics change.
- Incremental refresh uses the minimum available max `subscribed_dt` across the two source tables, subtracts `SUBSCRIPTION_SOURCE_INCREMENTAL_LOOKBACK_DAYS` currently 45 days, clamps to `cfg.subscriptions_start_date`, rounds to full months, and refreshes half-year blocks.
- The lookback is intentional. Trials can be subscribed in one month and charged in the next month, so old `subscribed_dt` partitions need to be reread after later charge events arrive.

Do not remove the lookback unless transaction logic changes to update by event date instead of subscription date.

## Experiment Users Contracts

`create_experiment_users_table(exp_info, client, segment_name, segment, ...)` creates or updates `exp_users_{exp_launch_id}`.

Important columns include:

- `unified_id`, `variation`, `exp_start_dt`, `client`, `segment`, `segment_hash`
- `payment_account_id`
- web/app version columns
- mobweb bridge columns: `app_unified_id`, `has_app`, `subscription_unified_ids`

`segment_hash` is part of cache invalidation. It depends on user filters and experiment context. If filters or relevant experiment timing/client context change, existing rows for that segment are deleted and rebuilt.

For `UG_WEB`, use helpers from `repository.py` instead of hand-rolling client logic:

- `base_client_for_calculation`
- `source_client_for_calculation`
- `expand_experiment_clients`
- `is_mobweb_segment`
- `web_event_platform_filter_sql`

## Monetization Contracts

`get_monetization_metrics(exp_info, exp_users_table, subscription_table, client, segment_name, segment_hash="", ...)` is a low-level SQL runner.

Preconditions:

- `exp_users_table` must point to an experiment users table with the requested `client`, `segment`, and `segment_hash`.
- `subscription_table` must be the joined transient table returned by `create_experiments_subscription_table(...)`.
- `trial_conversion_model` should exist and have at least one `update_dt`; the main pipeline calls `update_trial_conversion_model(...)` before monetization.

Output is a DataFrame of daily raw aggregates by `dt` and `variation`. `metrics.py` later converts these aggregates into cumulative metric values and pairwise stats according to `metrics.yaml`.

`charged_trial_cnt` and related revenue fields depend on `sta.charge_dt`, which comes from `subscriptions_transactions` through the joined temp table.

## Product Metrics Contracts

Retention and tab-view metrics read from `exp_users_table`, not from subscription tables.

- `get_retention_metrics(...)` calculates web/app/mobweb app retention.
- `get_tab_view_metrics(...)` calculates web/app/mobweb app tab-view metrics.
- For mobweb app product metrics, sampling is controlled by `ExperimentCalculatorConfig.mobweb_product_metrics_sample_rate` and env var `EXPERIMENT_MOBWEB_PRODUCT_METRICS_SAMPLE_RATE`.
- Product metrics use `domain: "product"` in YAML configs and reporting helpers.

Inside one `calculate_exp_info` run, product metrics are cached for segments with identical user filters. Segments that differ only by subscription filters can reuse product metric frames.

## Metrics and Stats

Metric configs are normalized from YAML list-of-singleton-dicts into plain dicts. Use `normalize_metric_config(...)` and `normalize_funnel_config(...)` rather than parsing manually.

To add a metric:

1. Add numerator/denominator/variance columns to the raw aggregate SQL or reuse existing columns.
2. Add the metric to `metrics.yaml`.
3. Set `sources`, optional `platforms`, optional `domain`, and `table_position`.
4. Ensure `calc_cumulative_aggregates(...)` and `calc_metrics_stats_by_variation_pairs(...)` receive the needed columns.

To add a stat shown in `ug_exp_stats`, add it to `stats.yaml` and make sure the raw/cumulative frame contains the corresponding metric column.

Pairwise comparisons are currently only `control variation 1 vs test variation N`.

## Result Table Writes

Result tables are:

- `ug_exp_stats`
- `ug_exp_results`
- `ug_exp_funnel_stats`
- `ug_exp_funnel_results`

The pipeline drops only partitions for the current `output_exp_id`, `client`, and `segment`, then inserts fresh rows. Previous launch windows use distinct negative numeric `output_exp_id` values and human-readable `exp_launch_id` strings.

Do not replace this with broad deletes or full table rewrites.

## Rollout Contracts

`calculate_rollout_share(...)` updates/reads split-user tables and returns cumulative experiment share by client/date.

`calculate_rollout_impact_estimate(...)` multiplies latest rollout share by average daily users. It can use either recent users from ClickHouse or a `daily_users_by_client` override.

Rollout writes use:

- per-experiment cache: `rollout_split_users_{exp_id}`
- aggregate table: `ug_exp_rollout_split_users`

The full experiment pipeline updates rollout only for the latest launch.

## Safe Development Checklist

Before editing:

- Read the relevant SQL template and its caller together.
- Check whether a method expects a raw source table, joined transient table, or result table.
- Preserve `segment_hash`, launch window, client expansion, and partitioning behavior unless the task explicitly changes them.
- Treat user/QA ClickHouse errors as possible wrong-table-contract errors before changing SQL fields.

After editing:

- Run `python -m compileall ug_experiment_calculator`.
- Run `git diff --check`.
- If dependencies are installed, import the touched public APIs from `.venv/bin/python`.
- If ClickHouse is available, run a small experiment or at least the specific helper on a known experiment.

## Common Failure Modes

- `no column 'sta.charge_dt' in table 'sta'`: `get_monetization_metrics` received raw `subscriptions`; create and pass `exp_subscription_*`.
- Missing newly added source fields: bump `SUBSCRIPTION_SOURCE_VERSION` and make sure both source SQL templates emit the expected schema.
- Trial charges missing across a month boundary: check source refresh lookback and whether `subscriptions_transactions` was refreshed for the subscription month.
- Duplicate or stale users: check `segment_hash`, launch window dates, and whether the client was removed during the experiment.
- Unexpected web/mobweb split: inspect `clients_options`, segment `platform`, and `expand_experiment_clients(...)`.
- Product metrics too slow for mobweb: check sampling settings and avoid disabling mobweb product sampling without a clear reason.

