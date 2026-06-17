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
    ) as exp_data
    

select
    `urew`.`unified_id`,
    `urew`.`experiments.variation`[indexOf(`urew`.`experiments.id`, `exp_id`)] as `variation`,
    min(toUnixTimestamp(`urew`.`datetime`)) AS `exp_start_dt`,
    argMin(`urew`.`rights`, `urew`.`datetime`) AS `rights`,
    argMin(`urew`.`user_id`, `urew`.`datetime`) AS `user_id`,
    toUInt32(0) AS `payment_account_id`,
    argMin(`urew`.`country`, `urew`.`datetime`) AS `country`,
    argMin(`urew`.`auth`, `urew`.`datetime`) AS `auth`,
    toInt64(0) AS `app_unified_id`,
    toUInt8(0) AS `has_app`,
    arrayDistinct(arrayFilter(x -> x > 0, [toInt64(`urew`.`unified_id`)])) AS `subscription_unified_ids`
    -- , [('platform', toString(argMin(`urew`.`platform`, `urew`.`datetime`))), ('value', toString(argMin(`urew`.`value`, `urew`.`datetime`)))] as `params`
from
    `default`.`ug_rt_events_web` as `urew`
where
    `urew`.`date` = `date_filter`
and
    `urew`.`datetime` between toDateTime(tupleElement(exp_data,2)) and if(tupleElement(exp_data,3) < tupleElement(exp_data,2), toDateTime(now()), toDateTime(tupleElement(exp_data,3)))
and
    `urew`.`unified_id` > 0
and
    (where_condition)
and
    `variation` > 0
and
    `urew`.`source` = '{client}'
and
    `urew`.`platform` = 1
group by
    `unified_id`,
    `variation`
having
    (having_condition)
and
    toDate(`exp_start_dt`, 'UTC') = `date_filter`
