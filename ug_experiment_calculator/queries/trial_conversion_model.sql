with
    toDate('{update_dt}') as `update_dt_value`,
    `sub_source` as (
        select
            *,
            countIf(`funnel_source` like '%Instant Offer%' and lower(`service_name`) like '%pro%') over (partition by `unified_id`) as `has_pro_instant_offer`,
            countIf(`funnel_source` like '%Instant Offer%' and lower(`service_name`) like '%book%') over (partition by `unified_id`) as `has_book_instant_offer`,
            groupArrayIf(`subscribed_dt`, `funnel_source` like '%Instant Offer%' and lower(`service_name`) like '%pro%') over (partition by `unified_id`) as `pro_instant_offer_sub_dts`,
            groupArrayIf(`subscribed_dt`, `funnel_source` like '%Instant Offer%' and lower(`service_name`) like '%book%') over (partition by `unified_id`) as `book_instant_offer_sub_dts`
        from (
            select
                if(
                    lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '',
                    `use`.`subscription_id`,
                    `use`.`orig`
                ) as `sub_key`,
                minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '')) as `subscribed_dt`,
                minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Charged') as `charge_dt`,
                argMinIf(`use`.`product_code`, `use`.`datetime`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '')) as `product_code`,
                argMinIf(`use`.`product_id`, `use`.`datetime`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '')) as `product_id`,
                argMinIf(`use`.`platform`, `use`.`datetime`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '')) as `platform`,
                argMinIf(`use`.`unified_id`, `use`.`datetime`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '')) as `unified_id`,
                argMinIf(`use`.`service_name`, `use`.`datetime`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '')) as `service_name`,
                argMinIf(`use`.`funnel_source`, `use`.`datetime`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '')) as `funnel_source`,
                argMinIf(`use`.`duration_count`, `use`.`datetime`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '')) as `duration_count`,
                argMinIf(`use`.`base_price`, `use`.`datetime`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '')) as `base_price`,
                argMinIf(`use`.`country`, `use`.`datetime`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '')) as `country`,
                argMinIf(
                    if(`use`.`datetime_next_billing` < `use`.`datetime`, toUnixTimestamp(`use`.`datetime`), toUnixTimestamp(`use`.`datetime_next_billing`)),
                    `use`.`datetime`,
                    `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '')
                ) as `first_charge_expected_dt`,
                greatest(
                    argMinIf(`use`.`trial`, `use`.`datetime`, `use`.`event` = 'Subscribed' and (lower(`use`.`platform`) like '%ios%' or `use`.`orig` = '')),
                    if(
                        toDate(`first_charge_expected_dt`) > toDate(`subscribed_dt`)
                        and toDate(`charge_dt`) != toDate(`subscribed_dt`),
                        dateDiff('day', toDate(`subscribed_dt`), toDate(`first_charge_expected_dt`)),
                        0
                    )
                ) as `trial`,
                toUInt8(countIf(lower(`use`.`platform`) not like '%ios%' and `use`.`orig` != '') > 0) as `is_access_intro`,
                if(
                    `is_access_intro` = 0
                    and (
                        (`duration_count` = 0 and `service_name` = '' and `trial` = 0)
                        or (`product_id` like 'onetime%' or `product_id` like '%|paid_trial')
                    ),
                    1,
                    0
                ) as `is_otp`
            from (
                select
                    *,
                    `params.str_value`[indexOf(`params.key`, 'original_subscription_id')] as `orig`
                from
                    `default`.`ug_subscriptions_events`
                where
                    `date` >= toStartOfMonth(today()) - interval 4 month
                and
                    `event` in ('Subscribed', 'Charged', 'Canceled', 'Refunded', 'Crossgrade', 'Upgrade', 'Downgrade', 'Autorenew Enabled')
            ) as `use`
            group by
                `sub_key`,
                if(
                    lower(`use`.`platform`) like '%ios%',
                    `use`.`product_code`,
                    0
                )
            having
                `product_code` > 0
            and
                lower(`funnel_source`) not like '%email%'
            and
                toDate(`subscribed_dt`) >= toStartOfMonth(today()) - interval 4 month
            and
                toDate(`subscribed_dt`) < toStartOfMonth(today()) - interval 1 month
        )
    ),
    `trials` as (
        select
            `src`.`platform` as `platform`,
            toInt32(ifNull(`cr`.`tier`, 0)) as `tier`,
            toInt32(round(`src`.`base_price`)) as `base_price_int`,
            toUInt8(
                (
                    `src`.`has_pro_instant_offer` > 0
                    and lower(`src`.`service_name`) like '%pro%'
                    and length(arrayFilter(x -> x between `src`.`subscribed_dt` and `src`.`subscribed_dt` + 86400, `src`.`pro_instant_offer_sub_dts`)) > 0
                )
                or (
                    `src`.`has_book_instant_offer` > 0
                    and lower(`src`.`service_name`) like '%book%'
                    and length(arrayFilter(x -> x between `src`.`subscribed_dt` and `src`.`subscribed_dt` + 86400, `src`.`book_instant_offer_sub_dts`)) > 0
                )
            ) as `is_trial2instant`,
            toUInt8(`src`.`charge_dt` between `src`.`subscribed_dt` and `src`.`first_charge_expected_dt` + 86400) as `converted`
        from
            `sub_source` as `src`
        left join
            `default`.`country_regions` as `cr`
        on
            `src`.`country` = `cr`.`country_code_a2`
        where
            `src`.`trial` > 0
        and
            `src`.`is_otp` = 0
        and
            `src`.`is_access_intro` = 0
        and
            not (toDate(`src`.`charge_dt`) = toDate(`src`.`subscribed_dt`))
    ),
    `cohorts` as (
        select
            `platform`,
            `tier`,
            `base_price_int`,
            count() as `n_trials`,
            sum(`converted`) as `n_converted`
        from
            `trials`
        where
            `is_trial2instant` = 0
        group by
            `platform`,
            `tier`,
            `base_price_int`
    ),
    `collapsed_cohorts` as (
        select
            `platform`,
            `tier`,
            if(`n_converted` < 20, toInt32(-1), `base_price_int`) as `base_price_int`,
            if(`n_converted` < 20, 'Other', toString(`base_price_int`)) as `base_price_label`,
            `n_trials` as `cohort_n_trials`,
            `n_converted` as `cohort_n_converted`
        from
            `cohorts`
    )
select
    `update_dt_value` as `update_dt`,
    `platform`,
    `tier`,
    `base_price_int`,
    any(`base_price_label`) as `base_price_label`,
    toUInt64(sum(`cohort_n_trials`)) as `n_trials`,
    toUInt64(sum(`cohort_n_converted`)) as `n_converted`,
    round(sum(`cohort_n_converted`) / sum(`cohort_n_trials`), 4) as `conversion`
from
    `collapsed_cohorts`
group by
    `platform`,
    `tier`,
    `base_price_int`
