with
    toDate('{date_start}') as date_start,
    toDate('{date_end}') as date_end

select
    `trx_event`.`subscription_id` as `subscription_id`,
    `trx_event`.`product_code` as `product_code`,
    `trx_event`.`subscribed_dt` as `subscribed_dt`,
    minIf(`trx_event`.`datetime_ts`, `trx_event`.`event` = 'Charged' and `trx_event`.`datetime_ts` > 0) as `charge_dt`,
    minIf(`trx_event`.`datetime_ts`, `trx_event`.`event` = 'Canceled' and `trx_event`.`datetime_ts` > 0) as `cancel_dt`,
    minIf(`trx_event`.`datetime_ts`, `trx_event`.`event` = 'Refunded' and `trx_event`.`datetime_ts` > 0) as `refund_dt`,
    minIf(`trx_event`.`datetime_ts`, `trx_event`.`event` in ('Upgrade', 'Crossgrade') and `trx_event`.`datetime_ts` > 0) as `upgrade_dt`,
    argMinIf(`trx_event`.`revenue_gross`, `trx_event`.`datetime_ts`, `trx_event`.`event` = 'Charged' and `trx_event`.`datetime_ts` > 0) as `revenue_gross`,
    argMinIf(`trx_event`.`revenue_gross`, `trx_event`.`datetime_ts`, `trx_event`.`event` = 'Refunded' and `trx_event`.`datetime_ts` > 0) as `refund_revenue_gross`,
    argMinIf(`trx_event`.`upgrade_revenue`, `trx_event`.`datetime_ts`, `trx_event`.`event` in ('Upgrade', 'Crossgrade') and `trx_event`.`datetime_ts` > 0) as `upgrade_revenue`,
    arraySort(
        x -> x.1,
        groupArrayIf(
            (`trx_event`.`event_date`, `trx_event`.`revenue_gross`),
            `trx_event`.`event` = 'Charged' and `trx_event`.`datetime_ts` > 0
        )
    ) as `all_charges_arr`,
    arrayFilter(
        (t, i) -> i = 1 or t.1 != `all_charges_arr`[i-1].1,
        `all_charges_arr`,
        arrayEnumerate(`all_charges_arr`)
    ) as `all_charges_arr_uniq`,
    now() as `updated_at`,
    toUInt16({source_version}) as `source_version`
from (
    select
        `sub`.`subscription_id` as `subscription_id`,
        `sub`.`product_code` as `product_code`,
        `sub`.`subscribed_dt` as `subscribed_dt`,
        `use`.`event` as `event`,
        `event_date`,
        `use`.`product_id` as `product_id`,
        `revenue_gross`,
        `upgrade_revenue`,
        minIf(
            toUnixTimestamp(`use`.`datetime`),
            toUnixTimestamp(`use`.`datetime`) >= `sub`.`subscribed_dt`
            and toUnixTimestamp(`use`.`datetime`) < `sub`.`next_subscribed_dt`
            and (
                (
                    lower(`use`.`platform`) not like '%ios%'
                    and `use`.`original_subscription_id` != ''
                )
                or `use`.`product_code` = `sub`.`product_code`
            )
        ) as `datetime_ts`
    from (
        select
            `subscription_id`,
            `product_code`,
            `subscribed_dt`,
            `next_subscribed_dt`
        from
            {subscriptions_table}
        where
            toDate(`subscribed_dt`) between date_start and date_end
    ) as `sub`
    left join (
        select
            `evt`.*,
            -- Web intro purchase: the intro subscription carries the param on its Subscribed and
            -- Charged rows only, never on its Refunded / Canceled ones. Resolve it per subscription
            -- (`itm`), so the refund of an intro charge lands on the base subscription too instead
            -- of a key with no subscription row.
            if(
                `evt`.`params.str_value`[indexOf(`evt`.`params.key`, 'original_subscription_id')] != '',
                `evt`.`params.str_value`[indexOf(`evt`.`params.key`, 'original_subscription_id')],
                ifNull(`itm`.`base_id`, '')
            ) as `original_subscription_id`,
            toDate(`evt`.`datetime`) as `event_date`,
            case
                when `evt`.`product_id` in ('com.ultimateguitar.tabs.plus.intro.1year', 'com.ultimateguitar.ugt.plus.intro.1year2', 'com.ultimateguitar.tabs.plus.1year7') then `evt`.`usd_price` * 19.99/39.99
                else `evt`.`usd_price`
            end as `revenue_gross`,
            -toFloat32OrZero(`evt`.`params.str_value`[indexOf(`evt`.`params.key`, 'usd_refund')]) as `upgrade_revenue`
        from
            `default`.`ug_subscriptions_events` as `evt`
        left join (
            -- intro subscription -> base subscription. The intro is bought together with the base,
            -- so its Subscribed / Charged rows are inside the same bound as the block's events.
            select
                `ims`.`subscription_id` as `intro_id`,
                max(`ims`.`params.str_value`[indexOf(`ims`.`params.key`, 'original_subscription_id')]) as `base_id`
            from
                `default`.`ug_subscriptions_events` as `ims`
            where
                `ims`.`event` in ('Subscribed', 'Charged')
            and
                lower(`ims`.`platform`) not like '%ios%'
            and
                has(`ims`.`params.key`, 'original_subscription_id')
            and
                `ims`.`date` >= date_start - interval 1 day
            group by
                `ims`.`subscription_id`
            having
                `base_id` != ''
        ) as `itm`
        on
            `evt`.`subscription_id` = `itm`.`intro_id`
        where
            `evt`.`event` in ('Charged', 'Canceled', 'Refunded', 'Crossgrade', 'Upgrade', 'Downgrade')
        -- Only events at or after subscribed_dt are attributed, so nothing before the block is needed.
        and
            `evt`.`date` >= date_start - interval 1 day
    ) as `use`
    on
        if(
            lower(`use`.`platform`) like '%ios%' or `use`.`original_subscription_id` = '',
            `use`.`subscription_id`,
            `use`.`original_subscription_id`
        ) = `sub`.`subscription_id`
    group by
        `sub`.`subscription_id`,
        `sub`.`product_code`,
        `sub`.`subscribed_dt`,
        `event`,
        `event_date`,
        `product_id`,
        `revenue_gross`,
        `upgrade_revenue`
) as `trx_event`
group by
    `trx_event`.`subscription_id`,
    `trx_event`.`product_code`,
    `trx_event`.`subscribed_dt`
