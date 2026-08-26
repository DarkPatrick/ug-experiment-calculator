with
    toDate('{events_start_date}') as `events_start_date`

select
    min(`urew`.`datetime`) as `first_event_dt`,
    max(`urew`.`datetime`) as `last_event_dt`,
    uniqExact(`urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'aix_variant_id')]) as `arms_cnt`,
    uniqExact(`urew`.`unified_id`) as `users_cnt`
from
    `default`.`ug_rt_events_web` as `urew`
where
    `urew`.`date` >= `events_start_date`
and
    `urew`.`event` = '{entry_event}'
and
    `urew`.`is_bot` = 0
and
    `urew`.`unified_id` > 0
and
    `urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'aix_experiment_id')] = {aix_experiment_id_sql}
