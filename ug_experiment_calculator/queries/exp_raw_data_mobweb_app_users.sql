with
    {exp_id} as `exp_id`,
    toDate('{date_filter}') as `date_filter`,
    toDate('{app_date_filter}') as `app_date_filter`,
    toDateTime({exp_start_ts}) as `exp_window_start_dt`,
    if({exp_end_ts} <= {exp_start_ts}, now(), toDateTime({exp_end_ts})) as `exp_window_end_dt`,
    toDate(`exp_window_end_dt`) as `exp_window_end_date`

select
    `wi`.`client` as `client`,
    `wi`.`segment` as `segment`,
    `wi`.`segment_hash` as `segment_hash`,
    `wi`.`unified_id` as `unified_id`,
    `wi`.`variation` as `variation`,
    `wi`.`exp_start_dt` as `exp_start_dt`,
    toInt64(argMin(`urea`.`unified_id`, `urea`.`datetime`)) as `app_unified_id`,
    toUInt64(argMin(`urea`.`payment_account_id`, `urea`.`datetime`)) as `app_payment_account_id`,
    min(`urea`.`datetime`) as `app_start_dt`
from
    {web_installs_table} as `wi`
inner join
    `default`.`ug_rt_events_app` as `urea`
on
    `urea`.`payment_account_id` = `wi`.`install_payment_account_id`
where
    `wi`.`client` = {client_sql}
and
    `wi`.`segment` = {segment_sql}
and
    `wi`.`segment_hash` = {segment_hash_sql}
and
    toDate(`wi`.`exp_start_dt`, 'UTC') = `date_filter`
and
    `urea`.`date` = `app_date_filter`
and
    `urea`.`datetime` between `wi`.`install_dt` - interval 5 minute and `exp_window_end_dt`
and
    `urea`.`unified_id` > 0
and
    `urea`.`payment_account_id` > 0
and
    `urea`.`event` = 'Tour Referral Start'
group by
    `client`,
    `segment`,
    `segment_hash`,
    `unified_id`,
    `variation`,
    `exp_start_dt`
