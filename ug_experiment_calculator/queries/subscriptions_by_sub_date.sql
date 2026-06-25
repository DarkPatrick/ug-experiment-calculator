with
    toDate('{date_start}') as date_start,
    toDate('{date_end}') as date_end,
    {where_sql} as `where_condition`,
    {having_sql} as `having_condition`

select
    *,
    `revenue_gross` * case
        when lower(`platform`) like '%ios%' then 0.7
        when lower(`platform`) like '%and%' then 0.85
        else 1
    end as `revenue`,
    `refund_revenue_gross` * case
        when lower(`platform`) like '%ios%' then 0.7
        when lower(`platform`) like '%and%' then 0.85
        else 1
    end as `refund_revenue`,
    arraySum(arrayMap(x -> x.2 * 
        case
            when lower(`platform`) like '%ios%' and x.1 >= toDate(`subscribed_dt`) and x.1 < toDate(`subscribed_dt`) + interval 1 year then 0.7
            when lower(`platform`) like '%ios%' or lower(`platform`) like '%and%' then 0.85
            else 1
        end
        , `all_charges_arr_uniq`)
    ) as `lifetime_revenue`,
    countIf(`funnel_source` like '%Instant Offer%' and lower(`service_name`) like '%pro%') over(partition by `unified_id`) as `has_pro_instant_offer`,
    countIf(`funnel_source` like '%Instant Offer%' and lower(`service_name`) like '%book%') over(partition by `unified_id`) as `has_book_instant_offer`,
    groupArrayIf(`subscribed_dt`, `funnel_source` like '%Instant Offer%' and lower(`service_name`) like '%pro%') over(partition by `unified_id`) as `pro_instant_offer_sub_dts`,
    groupArrayIf(`subscribed_dt`, `funnel_source` like '%Instant Offer%' and lower(`service_name`) like '%book%') over(partition by `unified_id`) as `book_instant_offer_sub_dts`
from (
    select
        if(
            `original_subscription_id` != '',
            `original_subscription_id`,
            `use`.`subscription_id`
        ) as `subscription_id`,
        argMinIf(`use`.`product_code`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `product_code`,
        minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `subscribed_dt`,
        -- IMPORTANT!: temporary condition
        -- minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` in ('Subscribed', 'Autorenew Enabled')) as `subscribed_dt`,
        minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Charged') as `charge_dt`,
        minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Canceled') as `cancel_dt`,
        minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Refunded') as `refund_dt`,
        minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Autorenew Enabled') as `reenable_dt`,
        minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` in ('Upgrade', 'Crossgrade')) as `upgrade_dt`,
        argMinIf(`use`.`platform`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `platform`,
        argMinIf(
            case
                when `use`.`datetime_next_billing` < `use`.`datetime` then toUnixTimestamp(`use`.`datetime`)
                else toUnixTimestamp(`use`.`datetime_next_billing`)
            end,
            `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = ''
        ) as `first_charge_expected_dt`,
        greatest(
            argMinIf(`use`.`trial`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = ''),
            if(
                toDate(`first_charge_expected_dt`) > toDate(`subscribed_dt`)
                and toDate(`charge_dt`) != toDate(`subscribed_dt`),
                dateDiff('day', toDate(`subscribed_dt`), toDate(`first_charge_expected_dt`)),
                0
            )
        ) as `trial`,
        argMinIf(`use`.`funnel_source`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `funnel_source`,
        argMinIf(`use`.`product_id`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `product_id`,
        argMinIf(`use`.`user_id`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `user_id`,
        argMinIf(`use`.`unified_id`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `unified_id`,
        argMinIf(`use`.`payment_account_id`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `payment_account_id`,
        argMinIf(`use`.`service_name`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `service_name`,
        argMinIf(`use`.`duration_count`, `use`.`datetime`, `use`.`event` = 'Subscribed' and `original_subscription_id` = '') as `duration_count`,
        toUInt8(countIf(`original_subscription_id` != '') > 0) as `is_access_intro`,
        if (
            `is_access_intro` = 0
            and (
                (`duration_count` = 0 and `service_name` = '' and `trial` = 0)
                    or (product_id like 'onetime%' or product_id like '%|paid_trial')
            )
            , 1, 0
        ) as `is_otp`,
        argMinIf(`use`.`usd_price`, `use`.`datetime`, `use`.`event` = 'Charged') as `revenue_gross`,
        argMinIf(
            case
                when `use`.`product_id` in ('com.ultimateguitar.tabs.plus.intro.1year', 'com.ultimateguitar.ugt.plus.intro.1year2', 'com.ultimateguitar.tabs.plus.1year7') then `use`.`usd_price` * 19.99/39.99
                else `use`.`usd_price`
            end, 
            `use`.`datetime`, `use`.`event` = 'Refunded'
        ) as `refund_revenue_gross`,
        argMinIf(-toFloat32OrZero(`use`.`params.str_value`[indexOf(`use`.`params.key`, 'usd_refund')]), `use`.`datetime`, `use`.`event` in ('Upgrade', 'Crossgrade')) as `upgrade_revenue`,
        groupArrayIf(
            (
                `use`.`date`,
                case
                    when `use`.`product_id` in ('com.ultimateguitar.tabs.plus.intro.1year', 'com.ultimateguitar.ugt.plus.intro.1year2', 'com.ultimateguitar.tabs.plus.1year7') then `use`.`usd_price` * 19.99/39.99
                    else `use`.`usd_price`
                end
            ), 
            `use`.`event` = 'Charged'
        ) as `all_charges_arr`,
        arrayFilter(
            (t, i) -> i = 1 or t.1 != `all_charges_arr`[i-1].1, 
            `all_charges_arr`, 
            arrayEnumerate(`all_charges_arr`)
        ) as `all_charges_arr_uniq`
    from (
        select
            *,
            `params.str_value`[indexOf(`params.key`, 'original_subscription_id')] as `original_subscription_id`
        from
            `default`.`ug_subscriptions_events`
    ) as `use`
    where
        `use`.`date` >= date_start - interval 15 day
    and
        `use`.`event` in ('Subscribed', 'Charged', 'Canceled', 'Refunded', 'Crossgrade', 'Upgrade', 'Downgrade', 'Autorenew Enabled')
    and
        (`where_condition`)
    group by
        if(
            `original_subscription_id` != '',
            `original_subscription_id`,
            `use`.`subscription_id`
        )
    having
        toDate(`subscribed_dt`) between date_start - interval 15 day and date_end
    and
        `product_code` > 0
    and
        lower(`funnel_source`) not like '%email%'
    and
        (`having_condition`)
)
