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
    `base_price`,
    `country`,
    `user_id`,
    `unified_id`,
    `payment_account_id`,
    `payment_account_id_vector`,
    `service_name`,
    `duration_count`,
    `is_access_intro`,
    `is_otp`,
    now() as `updated_at`,
    toUInt16({source_version}) as `source_version`
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
        `base_price`,
        `country`,
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
                lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '',
                `use`.`subscription_id`,
                `original_subscription_id`
            ) as `subscription_id`,
            argMinIf(`use`.`product_code`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `product_code`,
            minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `subscribed_dt`,
            minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Charged') as `same_day_charge_dt`,
            argMinIf(`use`.`platform`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `platform`,
            argMinIf(
                case
                    when `use`.`datetime_next_billing` < `use`.`datetime` then toUnixTimestamp(`use`.`datetime`)
                    else toUnixTimestamp(`use`.`datetime_next_billing`)
                end,
                `use`.`subscribed_pick_key`,
                `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')
            ) as `first_charge_expected_dt`,
            argMinIf(`use`.`trial`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `raw_trial`,
            -- UInt16: annual trials are 365 days, which a UInt8 column stored as 109.
            toUInt16(greatest(
                `raw_trial`,
                if(
                    toDate(`first_charge_expected_dt`) > toDate(`subscribed_dt`)
                    and toDate(`same_day_charge_dt`) != toDate(`subscribed_dt`),
                    dateDiff('day', toDate(`subscribed_dt`), toDate(`first_charge_expected_dt`)),
                    0
                )
            )) as `trial`,
            argMinIf(`use`.`funnel_source`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `funnel_source`,
            argMinIf(`use`.`product_id`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `product_id`,
            argMinIf(`use`.`base_price`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `base_price`,
            argMinIf(`use`.`country`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `country`,
            argMinIf(`use`.`user_id`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `user_id`,
            argMinIf(`use`.`unified_id`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `unified_id`,
            argMinIf(`use`.`payment_account_id`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `payment_account_id`,
            argMinIf(`use`.`payment_account_id_vector`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `payment_account_id_vector`,
            argMinIf(`use`.`service_name`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `service_name`,
            argMinIf(`use`.`duration_count`, `use`.`subscribed_pick_key`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '')) as `duration_count`,
            toUInt8(countIf(lower(`use`.`platform`) not like '%ios%' and `original_subscription_id` != '') > 0) as `is_access_intro`,
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
                -- Tie-break for argMinIf over Subscribed events: iOS sends two Subscribed events for
                -- one subscription in the same second with different accounts (one often with
                -- user_id = 0), and a datetime-only key picked either of them at random.
                tuple(
                    `datetime`,
                    `user_id` = 0,
                    `payment_account_id`,
                    cityHash64(
                        `user_id`, `unified_id`, `payment_account_id_vector`, `product_code`, `product_id`, `platform`,
                        `funnel_source`, `service_name`, `duration_count`, `trial`, `base_price`, `country`, `datetime_next_billing`
                    )
                ) as `subscribed_pick_key`,
                `params.str_value`[indexOf(`params.key`, 'original_subscription_id')] as `original_subscription_id`
            from
                `default`.`ug_subscriptions_events`
            where
                `date` >= date_start - interval 1 day
        ) as `use`
        where
            `use`.`event` in ('Subscribed', 'Charged')
        and (
            -- Rows are per subscription and day, so a block needs only its own days, plus the
            -- later Subscribed events of its subscriptions for next_subscribed_dt. Aggregating
            -- the whole history per block outgrew the 35 GiB query memory limit on 2026-11-01.
            `use`.`date` <= date_end + interval 1 day
            or (
                `use`.`event` = 'Subscribed'
                and if(
                    lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '',
                    `use`.`subscription_id`,
                    `original_subscription_id`
                ) in (
                    select
                        if(
                            lower(`blk`.`platform`) like '%ios%' or `blk`.`params.str_value`[indexOf(`blk`.`params.key`, 'original_subscription_id')] = '',
                            `blk`.`subscription_id`,
                            `blk`.`params.str_value`[indexOf(`blk`.`params.key`, 'original_subscription_id')]
                        ) as `block_subscription_id`
                    from
                        `default`.`ug_subscriptions_events` as `blk`
                    where
                        `blk`.`date` between date_start - interval 1 day and date_end + interval 1 day
                    and
                        `blk`.`event` = 'Subscribed'
                )
            )
        )
        group by
            if(
                lower(`use`.`platform`) like '%ios%' or `original_subscription_id` = '',
                `use`.`subscription_id`,
                `original_subscription_id`
            ),
            if(
                lower(`use`.`platform`) like '%ios%',
                `use`.`product_code`,
                0
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
