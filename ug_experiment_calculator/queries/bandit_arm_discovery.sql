with
    toDateTime({exp_start_ts}) as `exp_window_start_dt`,
    if({exp_end_ts} <= {exp_start_ts}, now(), toDateTime({exp_end_ts})) as `exp_window_end_dt`,
    `participate_events` as (
        select
            `urew`.`unified_id` as `unified_id`,
            `urew`.`datetime` as `event_dt`,
            `urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'aix_variant_id')] as `arm`
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
            not (lower(extractURLParameter(`urew`.`url`, 'utm_medium')) = 'crm' and lower(extractURLParameter(`urew`.`url`, 'utm_source')) = 'email')
        and
            `urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'aix_experiment_id')] = {aix_experiment_id_sql}
        and
            `urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'aix_variant_id')] != ''
    ),
    `user_first_touch` as (
        select
            `pev`.`unified_id` as `unified_id`,
            argMin(`pev`.`arm`, `pev`.`event_dt`) as `first_touch_arm`,
            uniqExact(`pev`.`arm`) as `arms_cnt`
        from
            `participate_events` as `pev`
        group by
            `unified_id`
    ),
    `arm_windows` as (
        select
            `pev`.`arm` as `arm`,
            min(`pev`.`event_dt`) as `first_seen_dt`,
            max(`pev`.`event_dt`) as `last_seen_dt`
        from
            `participate_events` as `pev`
        group by
            `arm`
    ),
    `arm_cohorts` as (
        select
            `uft`.`first_touch_arm` as `arm`,
            count() as `participants_cnt`,
            countIf(`uft`.`arms_cnt` > 1) as `arm_switchers_cnt`
        from
            `user_first_touch` as `uft`
        group by
            `arm`
    )

select
    `arw`.`arm` as `arm`,
    `arw`.`first_seen_dt` as `first_seen_dt`,
    `arw`.`last_seen_dt` as `last_seen_dt`,
    `arc`.`participants_cnt` as `participants_cnt`,
    `arc`.`arm_switchers_cnt` as `arm_switchers_cnt`
from
    `arm_windows` as `arw`
left join
    `arm_cohorts` as `arc`
on
    `arw`.`arm` = `arc`.`arm`
order by
    `first_seen_dt`,
    `arm`
