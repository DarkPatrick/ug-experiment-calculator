with
    toDate('{date_filter}') as `date_filter`,
    {where_sql} as `where_condition`,
    {having_sql} as `having_condition`,
    toDateTime({exp_start_ts}) as `exp_window_start_dt`,
    if({exp_end_ts} <= {exp_start_ts}, now(), toDateTime({exp_end_ts})) as `exp_window_end_dt`


select
    `urew`.`unified_id`,
    argMin(`urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'aix_variant_id')], `urew`.`datetime`) as `arm`,
    {arm_variation_sql} as `variation`,
    toUInt8(uniqExact(`urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'aix_variant_id')]) > 1) as `is_arm_switcher`,
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
    toUInt8(toDate(toDateTime(intDiv(toInt64(`urew`.`unified_id`), 1000000000)), 'UTC') = toDate(toDateTime(min(toUnixTimestamp(`urew`.`datetime`)), 'UTC'))) AS `is_new`,
    '' AS `connection`,
    '( Other )' AS `device_manufacturer`
from
    `default`.`ug_rt_events_web` as `urew`
where
    `urew`.`date` between toDate(`exp_window_start_dt`) and toDate(`exp_window_end_dt`)
and
    `urew`.`datetime` between `exp_window_start_dt` and `exp_window_end_dt`
and
    `urew`.`unified_id` > 0
and
    `urew`.`is_bot` = 0
and
    not (lower(extractURLParameter(`urew`.`url`, 'utm_medium')) = 'crm' and lower(extractURLParameter(`urew`.`url`, 'utm_source')) = 'email')
and
    `urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'aix_experiment_id')] = {aix_experiment_id_sql}
and
    `urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'aix_variant_id')] != ''
and
    (where_condition)
and
    `urew`.`source` = '{client}'
group by
    `unified_id`
having
    (having_condition)
and
    `variation` > 0
