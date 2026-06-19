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
    `is_otp`,
    now() as `updated_at`,
    toUInt16(6) as `source_version`
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
        `is_otp`
    from (
        select
            `use`.`subscription_id` as `subscription_id`,
            `use`.`product_code` as `product_code`,
            minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Subscribed') as `subscribed_dt`,
            minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Charged') as `same_day_charge_dt`,
            argMinIf(`use`.`platform`, `use`.`datetime`, `use`.`event` = 'Subscribed') as `platform`,
            argMinIf(
                case
                    when `use`.`datetime_next_billing` < `use`.`datetime` then toUnixTimestamp(`use`.`datetime`)
                    else toUnixTimestamp(`use`.`datetime_next_billing`)
                end,
                `use`.`datetime`,
                `use`.`event` = 'Subscribed'
            ) as `first_charge_expected_dt`,
            argMinIf(`use`.`trial`, `use`.`datetime`, `use`.`event` = 'Subscribed') as `raw_trial`,
            greatest(
                `raw_trial`,
                if(
                    toDate(`first_charge_expected_dt`) > toDate(`subscribed_dt`)
                    and toDate(`same_day_charge_dt`) != toDate(`subscribed_dt`),
                    dateDiff('day', toDate(`subscribed_dt`), toDate(`first_charge_expected_dt`)),
                    0
                )
            ) as `trial`,
            argMinIf(`use`.`funnel_source`, `use`.`datetime`, `use`.`event` = 'Subscribed') as `funnel_source`,
            argMinIf(`use`.`product_id`, `use`.`datetime`, `use`.`event` = 'Subscribed') as `product_id`,
            argMinIf(`use`.`user_id`, `use`.`datetime`, `use`.`event` = 'Subscribed') as `user_id`,
            argMinIf(`use`.`unified_id`, `use`.`datetime`, `use`.`event` = 'Subscribed') as `unified_id`,
            argMinIf(`use`.`payment_account_id`, `use`.`datetime`, `use`.`event` = 'Subscribed') as `payment_account_id`,
            argMinIf(`use`.`payment_account_id_vector`, `use`.`datetime`, `use`.`event` = 'Subscribed') as `payment_account_id_vector`,
            argMinIf(`use`.`service_name`, `use`.`datetime`, `use`.`event` = 'Subscribed') as `service_name`,
            argMinIf(`use`.`duration_count`, `use`.`datetime`, `use`.`event` = 'Subscribed') as `duration_count`,
            if (
                (`duration_count` = 0 and `service_name` = '' and `trial` = 0)
                    or (`product_id` like 'onetime%' or `product_id` like '%|paid_trial')
                , 1, 0
            ) as `is_otp`
        from
            `default`.`ug_subscriptions_events` as `use`
        where
            `use`.`event` in ('Subscribed', 'Charged')
        group by
            `subscription_id`,
            `product_code`,
            toDate(`use`.`datetime`)
        having
            `subscribed_dt` > 0
    )
)
where
    toDate(`subscribed_dt`) between date_start and date_end
