with
    {exp_id} as `exp_id`,
    toDate('{date_filter}') as `date_filter`,
    {where_sql} as `where_condition`,
    {having_sql} as `having_condition`,
    toDateTime({exp_start_ts}) as `exp_window_start_dt`,
    if({exp_end_ts} <= {exp_start_ts}, now(), toDateTime({exp_end_ts})) as `exp_window_end_dt`
    

select
    `urew`.`unified_id`,
    `urew`.`experiments.variation`[indexOf(`urew`.`experiments.id`, `exp_id`)] as `variation`,
    min(toUnixTimestamp(`urew`.`datetime`)) AS `exp_start_dt`,
    argMin(`urew`.`rights`, `urew`.`datetime`) AS `rights`,
    argMin(`urew`.`user_id`, `urew`.`datetime`) AS `user_id`,
    toUInt32(0) AS `payment_account_id`,
    argMin(`urew`.`country`, `urew`.`datetime`) AS `country`,
    toUInt8OrZero(toString(argMin(`urew`.`auth`, `urew`.`datetime`))) AS `auth`,
    toInt64(0) AS `app_unified_id`,
    toUInt8(0) AS `has_app`,
    arrayDistinct(arrayFilter(x -> x > 0, [toInt64(`urew`.`unified_id`)])) AS `subscription_unified_ids`,
    multiIf(lower(toString(argMin(`urew`.`os`, `urew`.`datetime`))) in ('android', 'ios'), lower(toString(argMin(`urew`.`os`, `urew`.`datetime`))), '( Other )') AS `os`,
    multiIf(lower(toString(argMin(`urew`.`browser`, `urew`.`datetime`))) in ('chrome', 'safari', 'bing', 'edge', 'firefox'), lower(toString(argMin(`urew`.`browser`, `urew`.`datetime`))), '( Other )') AS `browser`,
    if(empty(toString(argMin(`urew`.`frontend_release_version`, `urew`.`datetime`))), cast([], 'Array(UInt32)'), arrayMap(x -> toUInt32OrZero(x), splitByChar('.', toString(argMin(`urew`.`frontend_release_version`, `urew`.`datetime`))))) AS `frontend_release_version`,
    if(empty(toString(argMin(`urew`.`backend_release_version`, `urew`.`datetime`))), cast([], 'Array(UInt32)'), arrayMap(x -> toUInt32OrZero(x), splitByChar('.', toString(argMin(`urew`.`backend_release_version`, `urew`.`datetime`))))) AS `backend_release_version`,
    cast([], 'Array(UInt32)') AS `web_version`,
    toInt64OrZero(toString(argMin(`urew`.`platform`, `urew`.`datetime`))) AS `platform`,
    toString(argMin(`urew`.`type`, `urew`.`datetime`)) AS `type`,
    toUInt8(toDate(toDateTime(intDiv(toInt64(`urew`.`unified_id`), 1000000000)), 'UTC') = `date_filter`) AS `is_new`,
    '' AS `connection`,
    '( Other )' AS `device_manufacturer`
    -- , [('platform', toString(argMin(`urew`.`platform`, `urew`.`datetime`))), ('value', toString(argMin(`urew`.`value`, `urew`.`datetime`)))] as `params`
from
    `default`.`ug_rt_events_web` as `urew`
where
    `urew`.`date` = `date_filter`
and
    `urew`.`datetime` between `exp_window_start_dt` and `exp_window_end_dt`
and
    `urew`.`unified_id` > 0
and
    not (lower(extractURLParameter(`urew`.`url`, 'utm_medium')) = 'crm' and lower(extractURLParameter(`urew`.`url`, 'utm_source')) = 'email')
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
