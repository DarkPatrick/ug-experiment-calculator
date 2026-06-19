with
    `exp_users` as (
        select distinct
            *,
            if(
                empty(`subscription_unified_ids`),
                arrayDistinct(arrayFilter(x -> x > 0, [toInt64(`unified_id`), toInt64(`app_unified_id`)])),
                `subscription_unified_ids`
            ) as `subscription_join_unified_ids`
        from {exp_users_table}
        where
            `client` = {client_sql}
        and
            `segment` = {segment_sql}
        and
            `segment_hash` = {segment_hash_sql}
    ),
    `subscription_base` as (
        select distinct
            `sta`.*,
            arrayDistinct(
                arrayFilter(
                    x -> x > 0,
                    arrayFlatten(
                        groupArray(arrayPushFront(`sta`.`payment_account_id_vector`, `sta`.`payment_account_id`))
                            over (partition by `sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`))
                    )
                )
            ) as `payment_account_ids`
        from (
            select distinct *
            from {subscription_table}
        ) as `sta`
    ),
    `unified_matches` as (
        select distinct
            `sta`.*,
            `eum`.`exp_unified_id` as `exp_unified_id`
        from
            `subscription_base` as `sta`
        inner join (
            select distinct
                `unified_id` as `exp_unified_id`,
                arrayJoin(`subscription_join_unified_ids`) as `subscription_join_unified_id`
            from
                `exp_users`
        ) as `eum`
        on
            `sta`.`unified_id` = `eum`.`subscription_join_unified_id`
    ),
    `payment_account_matches` as (
        select distinct
            `sta`.*,
            `eum`.`exp_unified_id` as `exp_unified_id`
        from
            `subscription_base` as `sta`
        array join
            `sta`.`payment_account_ids` as `subscription_payment_account_id`
        inner join (
            select distinct
                `unified_id` as `exp_unified_id`,
                `payment_account_id`,
                `subscription_join_unified_ids`
            from
                `exp_users`
            where
                `payment_account_id` > 0
        ) as `eum`
        on
            `subscription_payment_account_id` = `eum`.`payment_account_id`
        where
            not has(`eum`.`subscription_join_unified_ids`, `sta`.`unified_id`)
    ),
    `subscription_matches` as (
        select * from `unified_matches`
        union distinct
        select * from `payment_account_matches`
    ),
    `subscriptions_per_user` as (
        select
            `dt`,
            `variation`,
            ifNotFinite(ifNull(varSamp(`subscriptions_per_user_cnt`), 0), 0) as `subscriptions_per_user_var`
        from (
            select
                toDate(`eut`.`exp_start_dt`) as `dt`,
                `eut`.`variation` as `variation`,
                `eut`.`unified_id` as `unified_id`,
                uniqIf(
                    (`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)),
                    `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800
                    and `sta`.`is_otp` = 0
                ) as `subscriptions_per_user_cnt`
            from
                `exp_users` as `eut`
            left join
                `subscription_matches` as `sta`
            on
                `eut`.`unified_id` = `sta`.`exp_unified_id`
            group by
                `dt`,
                `variation`,
                `unified_id`
        )
        group by
            `dt`,
            `variation`
    ),
    `charges_per_user` as (
        select
            `dt`,
            `variation`,
            ifNotFinite(ifNull(varSamp(`charges_per_user_cnt`), 0), 0) as `charges_per_user_var`
        from (
            select
                toDate(`eut`.`exp_start_dt`) as `dt`,
                `eut`.`variation` as `variation`,
                `eut`.`unified_id` as `unified_id`,
                uniqIf(
                    (`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)),
                    `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800
                    and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400
                    and `sta`.`is_otp` = 0
                )
                    + uniqIf(
                        (`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)),
                        `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800
                        and `sta`.`is_otp` = 1
                    ) as `charges_per_user_cnt`
            from
                `exp_users` as `eut`
            left join
                `subscription_matches` as `sta`
            on
                `eut`.`unified_id` = `sta`.`exp_unified_id`
            group by
                `dt`,
                `variation`,
                `unified_id`
        )
        group by
            `dt`,
            `variation`
    )

select
    toDate(`eut`.`exp_start_dt`) as `dt`,
    `eut`.`variation` as `variation`,
    uniq(`eut`.`unified_id`) as `members`,
    uniqIf(`eut`.`unified_id`, `eut`.`payment_account_id` > 0) as `install_cnt`,
    uniqIf(`eut`.`unified_id`, `eut`.`app_unified_id` > 0) as `app_referral_tour_cnt`,
    uniqIf(`eut`.`unified_id`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`is_otp` = 0) as `subscriber_cnt`,
    uniqIf(`eut`.`unified_id`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`is_otp` = 1) as `otp_owner_cnt`,
    uniqIf(`eut`.`unified_id`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800) as `access_owner_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`) and `sta`.`trial` = 0 and `sta`.`is_otp` = 0) as `access_instant_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`) and `sta`.`is_otp` = 0) as `access_ex_trial_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`is_otp` = 0) as `access_trial_cnt`,
    uniqIf(`eut`.`unified_id`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`is_otp` = 0) as `trial_subscriber_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` < `eut`.`exp_start_dt` and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`first_charge_expected_dt` > `eut`.`exp_start_dt` and `sta`.`is_otp` = 0 and (`sta`.`charge_dt` = 0 or `sta`.`charge_dt` > `eut`.`exp_start_dt`)) as `active_trial_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`cancel_dt` < `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` > toUnixTimestamp(now()) and `sta`.`is_otp` = 0) as `pending_trial_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`is_otp` = 1) as `access_otp_cnt`,
    `access_instant_cnt` + `access_ex_trial_cnt` + `access_trial_cnt` as `subscriptions_cnt`,
    any(`spu`.`subscriptions_per_user_var`) as `subscriptions_per_user_var`,
    `subscriptions_cnt` + `access_otp_cnt` as `access_cnt`,

    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0) as `charged_trial_cnt`,
    uniqIf(
        (`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)),
        `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`))
        and (
            `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400
            or `sta`.`has_pro_instant_offer` > 0 and lower(`sta`.`service_name`) like '%pro%' and length(arrayFilter(x -> x between `sta`.`subscribed_dt` and `sta`.`subscribed_dt` + 86400, `sta`.`pro_instant_offer_sub_dts`)) > 0
            or `sta`.`has_book_instant_offer` > 0 and lower(`sta`.`service_name`) like '%book%' and length(arrayFilter(x -> x between `sta`.`subscribed_dt` and `sta`.`subscribed_dt` + 86400, `sta`.`book_instant_offer_sub_dts`)) > 0
        ) and `sta`.`is_otp` = 0
    ) as `any_charged_trial_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` < `eut`.`exp_start_dt` and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0) as `active_charged_trial_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and `sta`.`charge_dt` = 0 and `sta`.`cancel_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0) as `cancel_trial_cnt`,
    uniqIf(`eut`.`unified_id`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0) as `trial_buyer_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` > `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0) as `late_charged_cnt`,
    uniqIf(`eut`.`unified_id`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0) as `subscribe_buyer_cnt`,
    uniqIf(`eut`.`unified_id`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400) as `buyer_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0) as `subscription_charge_cnt`,
    `subscription_charge_cnt` + `access_otp_cnt` as `charge_cnt`,
    any(`cpu`.`charges_per_user_var`) as `charges_per_user_var`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`refund_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600) as `refund_14d_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0 and `sta`.`charge_dt` > toUnixTimestamp(now()) - 1209600) as `pending_14d_charge_cnt`,
    coalesce(sumIf(`sta`.`revenue`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400), 0)
        - coalesce(sumIf(`sta`.`refund_revenue`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`refund_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600), 0) as `revenue`,
    coalesce(sumIf(`sta`.`refund_revenue`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`refund_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600), 0) as `refund_revenue`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` < `eut`.`exp_start_dt` and `sta`.`charge_dt` >= `sta`.`subscribed_dt`) as `recurrent_charge_cnt`,
    coalesce(sumIf(`sta`.`revenue`, `sta`.`subscribed_dt` < `eut`.`exp_start_dt` and `sta`.`charge_dt` >= `sta`.`subscribed_dt`), 0) as `recurrent_revenue`,
    coalesce(sumIf(`sta`.`revenue`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0), 0) as `trial_revenue`,
    coalesce(sumIf(`sta`.`revenue`, `sta`.`subscribed_dt` < `eut`.`exp_start_dt` and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0), 0) as `active_trial_revenue`,
    coalesce(sumIf(`sta`.`lifetime_revenue`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400), 0)
        - coalesce(sumIf(`sta`.`refund_revenue`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`refund_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600), 0) as `lifetime_revenue`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`upgrade_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800) as `upgrade_cnt`,
    coalesce(sumIf(`sta`.`upgrade_revenue`, `sta`.`upgrade_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800), 0) as `upgrade_revenue`,
    ifNotFinite(ifNull(varSamp(if(`sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0, `sta`.`revenue`, 0) - if(`sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`refund_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600, `sta`.`refund_revenue`, 0)), 0), 0) as `arpu_var`,
    ifNotFinite(ifNull(varSamp(if(`sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0, `sta`.`lifetime_revenue`, 0) - if(`sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`refund_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600, `sta`.`refund_revenue`, 0)), 0), 0) as `lifetime_arpu_var`,
    ifNotFinite(ifNull(varSampIf(if(`sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0, `sta`.`revenue`, 0) - if(`sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`refund_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600, `sta`.`refund_revenue`, 0), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0), 0), 0) as `arppu_var`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`cancel_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600) as `cancel_14d_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`cancel_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 2592000) as `cancel_1m_cnt`
from
    `exp_users` as `eut`
left join
    `subscription_matches` as `sta`
on
    `eut`.`unified_id` = `sta`.`exp_unified_id`
left join
    `subscriptions_per_user` as `spu`
on
    toDate(`eut`.`exp_start_dt`) = `spu`.`dt`
and
    `eut`.`variation` = `spu`.`variation`
left join
    `charges_per_user` as `cpu`
on
    toDate(`eut`.`exp_start_dt`) = `cpu`.`dt`
and
    `eut`.`variation` = `cpu`.`variation`
group by
    toDate(`eut`.`exp_start_dt`),
    `eut`.`variation`
order by
    `dt`,
    `variation`
