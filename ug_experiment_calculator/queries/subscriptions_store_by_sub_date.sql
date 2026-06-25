with
    toDate('{date_start}') as date_start,
    toDate('{date_end}') as date_end

select
    `subscription_id`,
    `product_code`,
    `subscribed_dt`,
    `next_subscribed_dt`,
    `platform`,
    `first_charge_expected_dt`,
    `trial`,
    `funnel_source`,
    `product_id`,
    `user_id`,
    `unified_id`,
    `payment_account_id`,
    `payment_account_id_vector`,
    `service_name`,
    `duration_count`,
    `is_access_intro`,
    `is_otp`,
    now() as `updated_at`,
    toUInt16(7) as `source_version`
from (
    select
        `subscription_id`,
        `product_code`,
        `subscribed_dt`,
        leadInFrame(`subscribed_dt`, 1, toUInt32(4102444800)) over (
            partition by
                `subscription_id`,
                `product_code`
            order by
                `subscribed_dt` asc
            rows between current row and unbounded following
        ) as `next_subscribed_dt`,
        `platform`,
        `first_charge_expected_dt`,
        `trial`,
        `funnel_source`,
        `product_id`,
        `user_id`,
        `unified_id`,
        `payment_account_id`,
        `payment_account_id_vector`,
        `service_name`,
        `duration_count`,
        `is_access_intro`,
        `is_otp`
    from (
        select
            if(
                `original_subscription_id` != '',
                `original_subscription_id`,
                `use`.`subscription_id`
            ) as `subscription_id`,
            argMinIf(`use`.`product_code`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `product_code`,
            minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `subscribed_dt`,
            minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Charged') as `same_day_charge_dt`,
            argMinIf(`use`.`platform`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `platform`,
            argMinIf(
                case
                    when `use`.`datetime_next_billing` < `use`.`datetime` then toUnixTimestamp(`use`.`datetime`)
                    else toUnixTimestamp(`use`.`datetime_next_billing`)
                end,
                `use`.`datetime`,
                `use`.`event` = 'Subscribed' and `original_subscription_id` = ''
            ) as `first_charge_expected_dt`,
            argMinIf(`use`.`trial`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `raw_trial`,
            greatest(
                `raw_trial`,
                if(
                    toDate(`first_charge_expected_dt`) > toDate(`subscribed_dt`)
                    and toDate(`same_day_charge_dt`) != toDate(`subscribed_dt`),
                    dateDiff('day', toDate(`subscribed_dt`), toDate(`first_charge_expected_dt`)),
                    0
                )
            ) as `trial`,
            argMinIf(`use`.`funnel_source`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `funnel_source`,
            argMinIf(`use`.`product_id`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `product_id`,
            argMinIf(`use`.`user_id`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `user_id`,
            argMinIf(`use`.`unified_id`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `unified_id`,
            argMinIf(`use`.`payment_account_id`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `payment_account_id`,
            argMinIf(`use`.`payment_account_id_vector`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `payment_account_id_vector`,
            argMinIf(`use`.`service_name`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `service_name`,
            argMinIf(`use`.`duration_count`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `duration_count`,
            toUInt8(countIf(`original_subscription_id` != '') > 0) as `is_access_intro`,
            if (
                `is_access_intro` = 0
                and (
                    (`duration_count` = 0 and `service_name` = '' and `trial` = 0)
                        or (`product_id` like 'onetime%' or `product_id` like '%|paid_trial')
                )
                , 1, 0
            ) as `is_otp`
        from (
            select
                *,
                `params.str_value`[indexOf(`params.key`, 'original_subscription_id')] as `original_subscription_id`
            from
                `default`.`ug_subscriptions_events`
        ) as `use`
        where
            `use`.`event` in ('Subscribed', 'Charged')
        group by
            if(
                `original_subscription_id` != '',
                `original_subscription_id`,
                `use`.`subscription_id`
            ),
            toDate(`use`.`datetime`)
        having
            `subscribed_dt` > 0
        and
            `product_code` > 0
    )
)
where
    toDate(`subscribed_dt`) between date_start and date_end
