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
    `wu`.`unified_id` = `wi`.`unified_id`
and
    `wu`.`variation` = `wi`.`variation`
left join
    (
        select
            `unified_id`,
            `variation`,
            argMin(`app_unified_id`, `app_start_dt`) as `app_unified_id`,
            argMin(`app_payment_account_id`, `app_start_dt`) as `app_payment_account_id`
        from
            {app_users_table}
        group by
            `unified_id`,
            `variation`
    ) as `au`
on
    `wu`.`unified_id` = `au`.`unified_id`
and
    `wu`.`variation` = `au`.`variation`
