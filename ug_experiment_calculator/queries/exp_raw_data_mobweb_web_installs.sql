with
    {exp_id} as `exp_id`,
    toDate('{date_filter}') as `date_filter`,
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
    `urew`.`date` between `date_filter` and `exp_end_date`
and
    `urew`.`datetime` between toDateTime(`wu`.`exp_start_dt`) and `exp_end_dt`
and
    `urew`.`event` = 'App Install'
and
    `urew`.`item_id` > 0
group by
    `unified_id`,
    `variation`
