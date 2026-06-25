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
        `use`.`subscription_id` as `subscription_id`,
        `use`.`product_code` as `product_code`,
        `use`.`subscribed_dt` as `subscribed_dt`,
        ifNull(`trx`.`charge_dt`, 0) as `charge_dt`,
        ifNull(`trx`.`cancel_dt`, 0) as `cancel_dt`,
        ifNull(`trx`.`refund_dt`, 0) as `refund_dt`,
        ifNull(`trx`.`upgrade_dt`, 0) as `upgrade_dt`,
        `use`.`platform` as `platform`,
        `use`.`first_charge_expected_dt` as `first_charge_expected_dt`,
        `use`.`trial` as `trial`,
        `use`.`funnel_source` as `funnel_source`,
        `use`.`product_id` as `product_id`,
        `use`.`user_id` as `user_id`,
        `use`.`unified_id` as `unified_id`,
        `use`.`payment_account_id` as `payment_account_id`,
        `use`.`payment_account_id_vector` as `payment_account_id_vector`,
        `use`.`service_name` as `service_name`,
        `use`.`duration_count` as `duration_count`,
        `use`.`is_access_intro` as `is_access_intro`,
        `use`.`is_otp` as `is_otp`,
        ifNull(`trx`.`revenue_gross`, 0) as `revenue_gross`,
        ifNull(`trx`.`refund_revenue_gross`, 0) as `refund_revenue_gross`,
        ifNull(`trx`.`upgrade_revenue`, 0) as `upgrade_revenue`,
        ifNull(`trx`.`all_charges_arr`, cast([], 'Array(Tuple(Date, Float64))')) as `all_charges_arr`,
        ifNull(`trx`.`all_charges_arr_uniq`, cast([], 'Array(Tuple(Date, Float64))')) as `all_charges_arr_uniq`
    from
        {subscriptions_table} as `use`
    left join
        {transactions_table} as `trx`
    on
        `use`.`subscription_id` = `trx`.`subscription_id`
    and
        `use`.`product_code` = `trx`.`product_code`
    and
        toDate(`use`.`subscribed_dt`) = toDate(`trx`.`subscribed_dt`)
    where
        toDate(`use`.`subscribed_dt`) between date_start - interval 15 day and date_end
    and
        lower(`use`.`funnel_source`) not like '%email%'
    and
        (`where_condition`)
    and
        (`having_condition`)
)
