with
    {exp_id} as `exp_id`,
    toDate('{date_filter}') as `date_filter`,
    {where_sql} as `where_condition`,
    {having_sql} as `having_condition`,
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
    toDate(`exp_end_dt`) as `exp_end_date`,

    web_users as (
        select
            `urew`.`unified_id` as `unified_id`,
            `urew`.`experiments.variation`[indexOf(`urew`.`experiments.id`, `exp_id`)] as `variation`,
            min(toUnixTimestamp(`urew`.`datetime`)) AS `exp_start_dt`,
            argMin(`urew`.`rights`, `urew`.`datetime`) AS `rights`,
            argMin(`urew`.`user_id`, `urew`.`datetime`) AS `user_id`,
            argMin(`urew`.`country`, `urew`.`datetime`) AS `country`,
            argMin(`urew`.`auth`, `urew`.`datetime`) AS `auth`
        from
            `default`.`ug_rt_events_web` as `urew`
        left join
            {exp_users_table} as `eut`
        on
            `urew`.`unified_id` = `eut`.`unified_id`
        and
            `eut`.`client` = {client_sql}
        and
            `eut`.`segment` = {segment_sql}
        and
            `eut`.`segment_hash` = {segment_hash_sql}
        where
            `urew`.`date` = `date_filter`
        and
            `urew`.`datetime` between toDateTime(tupleElement(exp_data,2)) and `exp_end_dt`
        and
            `urew`.`unified_id` > 0
        and
            `eut`.`unified_id` = 0
        and
            (where_condition)
        and
            `variation` > 0
        and
            `urew`.`source` = '{client}'
        and
            `urew`.`platform` > 1
        group by
            `unified_id`,
            `variation`
        having
            (having_condition)
        and
            toDate(`exp_start_dt`, 'UTC') = `date_filter`
    ),

    web_installs as (
        select
            `wu`.`unified_id` as `unified_id`,
            `wu`.`variation` as `variation`,
            argMin(`urew`.`item_id`, `urew`.`datetime`) as `install_payment_account_id`,
            min(`urew`.`datetime`) as `install_dt`
        from
            web_users as `wu`
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
    ),

    app_users as (
        select
            `wi`.`unified_id` as `unified_id`,
            `wi`.`variation` as `variation`,
            argMin(`urea`.`unified_id`, `urea`.`datetime`) as `app_unified_id`,
            argMin(`urea`.`payment_account_id`, `urea`.`datetime`) as `app_payment_account_id`,
            min(`urea`.`datetime`) as `app_start_dt`
        from
            web_installs as `wi`
        inner join
            `default`.`ug_rt_events_app` as `urea`
        on
            `urea`.`payment_account_id` = `wi`.`install_payment_account_id`
        where
            `urea`.`date` between toDate(`wi`.`install_dt`) and `exp_end_date`
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
    )

select
    `wu`.`unified_id` as `unified_id`,
    `wu`.`variation` as `variation`,
    `wu`.`exp_start_dt` as `exp_start_dt`,
    `wu`.`rights` as `rights`,
    `wu`.`user_id` as `user_id`,
    if(`au`.`app_payment_account_id` > 0, `au`.`app_payment_account_id`, ifNull(`wi`.`install_payment_account_id`, 0)) as `payment_account_id`,
    `wu`.`country` as `country`,
    `wu`.`auth` as `auth`,
    ifNull(`au`.`app_unified_id`, 0) as `app_unified_id`,
    toUInt8(ifNull(`au`.`app_unified_id`, 0) > 0) as `has_app`,
    arrayDistinct(arrayFilter(x -> x > 0, [toInt64(`wu`.`unified_id`), toInt64(ifNull(`au`.`app_unified_id`, 0))])) as `subscription_unified_ids`
from
    web_users as `wu`
left join
    web_installs as `wi`
on
    `wu`.`unified_id` = `wi`.`unified_id`
and
    `wu`.`variation` = `wi`.`variation`
left join
    app_users as `au`
on
    `wu`.`unified_id` = `au`.`unified_id`
and
    `wu`.`variation` = `au`.`variation`
