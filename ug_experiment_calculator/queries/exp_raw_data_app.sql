with
    {exp_id} as `exp_id`,
    toDate('{date_filter}') as `date_filter`,
    {where_sql} as `where_condition`,
    {having_sql} as `having_condition`,
    toDateTime({exp_start_ts}) as `exp_window_start_dt`,
    if({exp_end_ts} <= {exp_start_ts}, now(), toDateTime({exp_end_ts})) as `exp_window_end_dt`
    

select
    `urea`.`unified_id`,
    `urea`.`experiments.variation`[indexOf(`urea`.`experiments.id`, `exp_id`)] as `variation`,
    min(toUnixTimestamp(`urea`.`datetime`)) AS `exp_start_dt`,
    argMin(`urea`.`rights`, `urea`.`datetime`) AS `rights`,
    argMin(`urea`.`user_id`, `urea`.`datetime`) AS `user_id`,
    argMin(`urea`.`payment_account_id`, `urea`.`datetime`) AS `payment_account_id`,
    argMin(`urea`.`country`, `urea`.`datetime`) AS `country`,
    argMin(`urea`.`auth`, `urea`.`datetime`) AS `auth`,
    toInt64(0) AS `app_unified_id`,
    toUInt8(1) AS `has_app`,
    arrayDistinct(arrayFilter(x -> x > 0, [toInt64(`urea`.`unified_id`)])) AS `subscription_unified_ids`,
    multiIf(lower(toString(argMin(`urea`.`os`, `urea`.`datetime`))) in ('android', 'ios', 'os x', 'windows'), lower(toString(argMin(`urea`.`os`, `urea`.`datetime`))), '( Other )') AS `os`,
    '' AS `browser`,
    cast([], 'Array(UInt32)') AS `frontend_release_version`,
    cast([], 'Array(UInt32)') AS `backend_release_version`,
    if(empty(toString(argMin(`urea`.`app_version`, `urea`.`datetime`))), cast([], 'Array(UInt32)'), arrayMap(x -> toUInt32OrZero(x), splitByChar('.', toString(argMin(`urea`.`app_version`, `urea`.`datetime`))))) AS `web_version`,
    toInt64OrZero(toString(argMin(`urea`.`platform`, `urea`.`datetime`))) AS `platform`,
    toString(argMin(`urea`.`type`, `urea`.`datetime`)) AS `type`,
    toUInt8(toDate(toDateTime(intDiv(toInt64(`urea`.`unified_id`), 1000000000)), 'UTC') = `date_filter`) AS `is_new`,
    toString(argMin(`urea`.`connection`, `urea`.`datetime`)) AS `connection`,
    multiIf(
        lower(toString(argMin(`urea`.`device_manufacturer`, `urea`.`datetime`))) = 'apple', 'Apple',
        lower(toString(argMin(`urea`.`device_manufacturer`, `urea`.`datetime`))) = 'samsung', 'samsung',
        lower(toString(argMin(`urea`.`device_manufacturer`, `urea`.`datetime`))) = 'xiaomi', 'Xiaomi',
        lower(toString(argMin(`urea`.`device_manufacturer`, `urea`.`datetime`))) = 'google', 'Google',
        '( Other )'
    ) AS `device_manufacturer`
    -- , [('platform', toString(argMin(`urea`.`platform`, `urea`.`datetime`))), ('value', toString(argMin(`urea`.`value`, `urea`.`datetime`)))] as `params`
from
    `default`.`ug_rt_events_app` as `urea`
where
    `urea`.`date` = `date_filter`
and
    `urea`.`datetime` between `exp_window_start_dt` and `exp_window_end_dt`
and
    `urea`.`unified_id` > 0
and
    (where_condition)
and
    `variation` > 0
and
    `urea`.`source` = '{client}'
group by
    `unified_id`,
    `variation`
having
    (having_condition)
and
    toDate(`exp_start_dt`, 'UTC') = `date_filter`
