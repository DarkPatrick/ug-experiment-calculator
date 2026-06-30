with
    {exp_id} as `exp_id`,
    toDate('{date_filter}') as `date_filter`,
    toDateTime({exp_start_ts}) as `exp_window_start_dt`,
    if({exp_end_ts} <= {exp_start_ts}, now(), toDateTime({exp_end_ts})) as `exp_window_end_dt`,
    toDate(`exp_window_end_dt`) as `exp_window_end_date`

select
    `wu`.`unified_id` as `unified_id`,
    `wu`.`variation` as `variation`,
    toUInt64(argMin(`urew`.`item_id`, `urew`.`datetime`)) as `install_payment_account_id`,
    min(`urew`.`datetime`) as `install_dt`
from
    {web_users_table} as `wu`
inner join
    `default`.`ug_rt_events_web` as `urew`
on
    `urew`.`unified_id` = `wu`.`unified_id`
where
    `urew`.`date` between `date_filter` and `exp_window_end_date`
and
    `urew`.`datetime` between toDateTime(`wu`.`exp_start_dt`) and `exp_window_end_dt`
and
    `urew`.`event` = 'App Install'
and
    `urew`.`item_id` > 0
group by
    `unified_id`,
    `variation`
