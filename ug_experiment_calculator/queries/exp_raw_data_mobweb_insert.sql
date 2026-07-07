with
    toDate('{date_filter}') as `date_filter`

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
    arrayDistinct(arrayFilter(x -> x > 0, [toInt64(`wu`.`unified_id`), toInt64(ifNull(`au`.`app_unified_id`, 0))])) as `subscription_unified_ids`,
    `wu`.`os` as `os`,
    `wu`.`browser` as `browser`,
    `wu`.`frontend_release_version` as `frontend_release_version`,
    `wu`.`backend_release_version` as `backend_release_version`,
    `wu`.`web_version` as `web_version`,
    `wu`.`platform` as `platform`,
    `wu`.`type` as `type`,
    `wu`.`is_new` as `is_new`,
    `wu`.`connection` as `connection`,
    `wu`.`device_manufacturer` as `device_manufacturer`
from
    {web_users_table} as `wu`
left join
    {web_installs_table} as `wi`
on
    `wu`.`client` = `wi`.`client`
and
    `wu`.`segment` = `wi`.`segment`
and
    `wu`.`segment_hash` = `wi`.`segment_hash`
and
    `wu`.`unified_id` = `wi`.`unified_id`
and
    `wu`.`variation` = `wi`.`variation`
and
    `wu`.`exp_start_dt` = `wi`.`exp_start_dt`
left join
    (
        select
            `client`,
            `segment`,
            `segment_hash`,
            `unified_id`,
            `variation`,
            `exp_start_dt`,
            argMin(`app_unified_id`, `app_start_dt`) as `app_unified_id`,
            argMin(`app_payment_account_id`, `app_start_dt`) as `app_payment_account_id`
        from
            {app_users_table}
        group by
            `client`,
            `segment`,
            `segment_hash`,
            `unified_id`,
            `variation`,
            `exp_start_dt`
    ) as `au`
on
    `wu`.`client` = `au`.`client`
and
    `wu`.`segment` = `au`.`segment`
and
    `wu`.`segment_hash` = `au`.`segment_hash`
and
    `wu`.`unified_id` = `au`.`unified_id`
and
    `wu`.`variation` = `au`.`variation`
and
    `wu`.`exp_start_dt` = `au`.`exp_start_dt`
where
    `wu`.`client` = {client_sql}
and
    `wu`.`segment` = {segment_sql}
and
    `wu`.`segment_hash` = {segment_hash_sql}
and
    toDate(`wu`.`exp_start_dt`, 'UTC') = `date_filter`
