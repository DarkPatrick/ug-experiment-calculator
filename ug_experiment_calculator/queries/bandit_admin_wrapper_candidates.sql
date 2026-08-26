with
    toDateTime({exp_start_ts}) as `exp_window_start_dt`,
    if({exp_end_ts} <= {exp_start_ts}, now(), toDateTime({exp_end_ts})) as `exp_window_end_dt`,
    `participate_events` as (
        select
            `urew`.`unified_id` as `unified_id`,
            `urew`.`experiments.id` as `admin_exp_ids`,
            `urew`.`experiments.variation` as `admin_variations`
        from
            `default`.`ug_rt_events_web` as `urew`
        where
            `urew`.`date` between toDate(`exp_window_start_dt`) and toDate(`exp_window_end_dt`)
        and
            `urew`.`datetime` between `exp_window_start_dt` and `exp_window_end_dt`
        and
            `urew`.`event` = '{entry_event}'
        and
            `urew`.`is_bot` = 0
        and
            `urew`.`unified_id` > 0
        and
            `urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'aix_experiment_id')] = {aix_experiment_id_sql}
    ),
    `total_participants` as (
        select
            uniqExact(`pev`.`unified_id`) as `participants_total`
        from
            `participate_events` as `pev`
    ),
    `exploded` as (
        select
            `pev`.`unified_id` as `unified_id`,
            `admin_exp_id`,
            `admin_variation`
        from
            `participate_events` as `pev`
        array join
            `pev`.`admin_exp_ids` as `admin_exp_id`,
            `pev`.`admin_variations` as `admin_variation`
        where
            `admin_variation` > 0
    )

select
    `exd`.`admin_exp_id` as `admin_exp_id`,
    uniqExact(`exd`.`unified_id`) as `covered_users`,
    uniqExact(`exd`.`unified_id`) / (select `participants_total` from `total_participants`) as `coverage_share`,
    uniqExactIf(`exd`.`unified_id`, `exd`.`admin_variation` = 1) / uniqExact(`exd`.`unified_id`) as `holdout_share`,
    uniqExact(`exd`.`admin_variation`) as `variations_cnt`
from
    `exploded` as `exd`
group by
    `admin_exp_id`
having
    `variations_cnt` >= 2
and
    `coverage_share` >= 0.5
order by
    `holdout_share` asc,
    `covered_users` desc
limit 20
