with
    {exp_id} as `exp_id`,
    toDate('{app_date_filter}') as `app_date_filter`,
    (
        select distinct
            toUInt32(`aee`.`id`) as `id`,
            `aee`.`date_start` as `date_start`,
            `aee`.`date_end` as `date_end`
        from
            `mysql_u_guitarcom`.`ab_experiment_export` as `aee`
        where
            `aee`.`product` = 'UG'
        and
            `aee`.`id` = `exp_id`
        limit 1
    ) as exp_data,
    if(tupleElement(exp_data,3) < tupleElement(exp_data,2), toDateTime(now()), toDateTime(tupleElement(exp_data,3))) as `exp_end_dt`,
    toDate(`exp_end_dt`) as `exp_end_date`

select
    `wi`.`unified_id` as `unified_id`,
    `wi`.`variation` as `variation`,
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
    `urea`.`date` = `app_date_filter`
and
    `urea`.`datetime` between `wi`.`install_dt` and `exp_end_dt`
and
    `urea`.`unified_id` > 0
and
    `urea`.`payment_account_id` > 0
and
    `urea`.`event` = 'Tour Referral Start'
group by
    `unified_id`,
    `variation`
