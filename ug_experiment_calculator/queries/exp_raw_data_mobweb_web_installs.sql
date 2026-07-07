with
    {exp_id} as `exp_id`,
    toDate('{date_filter}') as `date_filter`,
    toDateTime({exp_start_ts}) as `exp_window_start_dt`,
    if({exp_end_ts} <= {exp_start_ts}, now(), toDateTime({exp_end_ts})) as `exp_window_end_dt`,
    toDate(`exp_window_end_dt`) as `exp_window_end_date`

select
    `wu`.`client` as `client`,
    `wu`.`segment` as `segment`,
    `wu`.`segment_hash` as `segment_hash`,
    `wu`.`unified_id` as `unified_id`,
    `wu`.`variation` as `variation`,
    `wu`.`exp_start_dt` as `exp_start_dt`,
    toUInt64(argMin(`urew`.`item_id`, `urew`.`datetime`)) as `install_payment_account_id`,
    min(`urew`.`datetime`) as `install_dt`
from
    {web_users_table} as `wu`
inner join
    `default`.`ug_rt_events_web` as `urew`
on
    `urew`.`unified_id` = `wu`.`unified_id`
where
    `wu`.`client` = {client_sql}
and
    `wu`.`segment` = {segment_sql}
and
    `wu`.`segment_hash` = {segment_hash_sql}
and
    toDate(`wu`.`exp_start_dt`, 'UTC') = `date_filter`
and
    `urew`.`date` between `date_filter` and `exp_window_end_date`
and
    `urew`.`datetime` between toDateTime(`wu`.`exp_start_dt`) and `exp_window_end_dt`
and
    `urew`.`event` = 'App Install'
and
    `urew`.`item_id` > 0
group by
    `client`,
    `segment`,
    `segment_hash`,
    `unified_id`,
    `variation`,
    `exp_start_dt`
