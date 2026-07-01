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
            `sta`.`subscription_id` as `subscription_id`,
            `sta`.`product_code` as `product_code`,
            `sta`.`subscribed_dt` as `subscribed_dt`,
            `sta`.`charge_dt` as `charge_dt`,
            `sta`.`cancel_dt` as `cancel_dt`,
            `sta`.`refund_dt` as `refund_dt`,
            `sta`.`upgrade_dt` as `upgrade_dt`,
            `sta`.`platform` as `platform`,
            `sta`.`first_charge_expected_dt` as `first_charge_expected_dt`,
            `sta`.`trial` as `trial`,
            `sta`.`funnel_source` as `funnel_source`,
            `sta`.`product_id` as `product_id`,
            `sta`.`base_price` as `base_price`,
            `sta`.`country` as `country`,
            `sta`.`user_id` as `user_id`,
            `sta`.`unified_id` as `unified_id`,
            `sta`.`payment_account_id` as `payment_account_id`,
            `sta`.`payment_account_id_vector` as `payment_account_id_vector`,
            `sta`.`service_name` as `service_name`,
            `sta`.`duration_count` as `duration_count`,
            `sta`.`is_access_intro` as `is_access_intro`,
            `sta`.`is_otp` as `is_otp`,
            `sta`.`revenue_gross` as `revenue_gross`,
            `sta`.`refund_revenue_gross` as `refund_revenue_gross`,
            `sta`.`upgrade_revenue` as `upgrade_revenue`,
            `sta`.`all_charges_arr` as `all_charges_arr`,
            `sta`.`all_charges_arr_uniq` as `all_charges_arr_uniq`,
            `sta`.`revenue` as `revenue`,
            `sta`.`refund_revenue` as `refund_revenue`,
            `sta`.`lifetime_revenue` as `lifetime_revenue`,
            `sta`.`has_pro_instant_offer` as `has_pro_instant_offer`,
            `sta`.`has_book_instant_offer` as `has_book_instant_offer`,
            `sta`.`pro_instant_offer_sub_dts` as `pro_instant_offer_sub_dts`,
            `sta`.`book_instant_offer_sub_dts` as `book_instant_offer_sub_dts`,
            arrayDistinct(
                arrayFilter(
                    x -> x > 0,
                    arrayFlatten(
                        groupArray(arrayPushFront(`payment_account_id_vector`, `payment_account_id`))
                            over (partition by `subscription_id`, `product_id`, toDate(`subscribed_dt`))
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
            `sta`.`subscription_id` as `subscription_id`,
            `sta`.`product_code` as `product_code`,
            `sta`.`subscribed_dt` as `subscribed_dt`,
            `sta`.`charge_dt` as `charge_dt`,
            `sta`.`cancel_dt` as `cancel_dt`,
            `sta`.`refund_dt` as `refund_dt`,
            `sta`.`upgrade_dt` as `upgrade_dt`,
            `sta`.`platform` as `platform`,
            `sta`.`first_charge_expected_dt` as `first_charge_expected_dt`,
            `sta`.`trial` as `trial`,
            `sta`.`funnel_source` as `funnel_source`,
            `sta`.`product_id` as `product_id`,
            `sta`.`base_price` as `base_price`,
            `sta`.`country` as `country`,
            `sta`.`user_id` as `user_id`,
            `sta`.`unified_id` as `unified_id`,
            `sta`.`payment_account_id` as `payment_account_id`,
            `sta`.`payment_account_id_vector` as `payment_account_id_vector`,
            `sta`.`payment_account_ids` as `payment_account_ids`,
            `sta`.`service_name` as `service_name`,
            `sta`.`duration_count` as `duration_count`,
            `sta`.`is_access_intro` as `is_access_intro`,
            `sta`.`is_otp` as `is_otp`,
            `sta`.`revenue_gross` as `revenue_gross`,
            `sta`.`refund_revenue_gross` as `refund_revenue_gross`,
            `sta`.`upgrade_revenue` as `upgrade_revenue`,
            `sta`.`all_charges_arr` as `all_charges_arr`,
            `sta`.`all_charges_arr_uniq` as `all_charges_arr_uniq`,
            `sta`.`revenue` as `revenue`,
            `sta`.`refund_revenue` as `refund_revenue`,
            `sta`.`lifetime_revenue` as `lifetime_revenue`,
            `sta`.`has_pro_instant_offer` as `has_pro_instant_offer`,
            `sta`.`has_book_instant_offer` as `has_book_instant_offer`,
            `sta`.`pro_instant_offer_sub_dts` as `pro_instant_offer_sub_dts`,
            `sta`.`book_instant_offer_sub_dts` as `book_instant_offer_sub_dts`,
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
            `sta`.`subscription_id` as `subscription_id`,
            `sta`.`product_code` as `product_code`,
            `sta`.`subscribed_dt` as `subscribed_dt`,
            `sta`.`charge_dt` as `charge_dt`,
            `sta`.`cancel_dt` as `cancel_dt`,
            `sta`.`refund_dt` as `refund_dt`,
            `sta`.`upgrade_dt` as `upgrade_dt`,
            `sta`.`platform` as `platform`,
            `sta`.`first_charge_expected_dt` as `first_charge_expected_dt`,
            `sta`.`trial` as `trial`,
            `sta`.`funnel_source` as `funnel_source`,
            `sta`.`product_id` as `product_id`,
            `sta`.`base_price` as `base_price`,
            `sta`.`country` as `country`,
            `sta`.`user_id` as `user_id`,
            `sta`.`unified_id` as `unified_id`,
            `sta`.`payment_account_id` as `payment_account_id`,
            `sta`.`payment_account_id_vector` as `payment_account_id_vector`,
            `sta`.`payment_account_ids` as `payment_account_ids`,
            `sta`.`service_name` as `service_name`,
            `sta`.`duration_count` as `duration_count`,
            `sta`.`is_access_intro` as `is_access_intro`,
            `sta`.`is_otp` as `is_otp`,
            `sta`.`revenue_gross` as `revenue_gross`,
            `sta`.`refund_revenue_gross` as `refund_revenue_gross`,
            `sta`.`upgrade_revenue` as `upgrade_revenue`,
            `sta`.`all_charges_arr` as `all_charges_arr`,
            `sta`.`all_charges_arr_uniq` as `all_charges_arr_uniq`,
            `sta`.`revenue` as `revenue`,
            `sta`.`refund_revenue` as `refund_revenue`,
            `sta`.`lifetime_revenue` as `lifetime_revenue`,
            `sta`.`has_pro_instant_offer` as `has_pro_instant_offer`,
            `sta`.`has_book_instant_offer` as `has_book_instant_offer`,
            `sta`.`pro_instant_offer_sub_dts` as `pro_instant_offer_sub_dts`,
            `sta`.`book_instant_offer_sub_dts` as `book_instant_offer_sub_dts`,
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
    `latest_trial_conversion_model` as (
        select
            `platform`,
            `tier`,
            `base_price_int`,
            `conversion`
        from
            {trial_conversion_model_table}
        where
            `update_dt` = (
                select max(`update_dt`)
                from {trial_conversion_model_table}
            )
    ),
    `subscription_matches_with_expected` as (
        select
            `sta`.`subscription_id` as `subscription_id`,
            `sta`.`product_code` as `product_code`,
            `sta`.`subscribed_dt` as `subscribed_dt`,
            `sta`.`charge_dt` as `charge_dt`,
            `sta`.`cancel_dt` as `cancel_dt`,
            `sta`.`refund_dt` as `refund_dt`,
            `sta`.`upgrade_dt` as `upgrade_dt`,
            `sta`.`platform` as `platform`,
            `sta`.`first_charge_expected_dt` as `first_charge_expected_dt`,
            `sta`.`trial` as `trial`,
            `sta`.`funnel_source` as `funnel_source`,
            `sta`.`product_id` as `product_id`,
            `sta`.`base_price` as `base_price`,
            `sta`.`country` as `country`,
            `sta`.`user_id` as `user_id`,
            `sta`.`unified_id` as `unified_id`,
            `sta`.`payment_account_id` as `payment_account_id`,
            `sta`.`payment_account_id_vector` as `payment_account_id_vector`,
            `sta`.`payment_account_ids` as `payment_account_ids`,
            `sta`.`service_name` as `service_name`,
            `sta`.`duration_count` as `duration_count`,
            `sta`.`is_access_intro` as `is_access_intro`,
            `sta`.`is_otp` as `is_otp`,
            `sta`.`revenue_gross` as `revenue_gross`,
            `sta`.`refund_revenue_gross` as `refund_revenue_gross`,
            `sta`.`upgrade_revenue` as `upgrade_revenue`,
            `sta`.`all_charges_arr` as `all_charges_arr`,
            `sta`.`all_charges_arr_uniq` as `all_charges_arr_uniq`,
            `sta`.`revenue` as `revenue`,
            `sta`.`refund_revenue` as `refund_revenue`,
            `sta`.`lifetime_revenue` as `lifetime_revenue`,
            `sta`.`has_pro_instant_offer` as `has_pro_instant_offer`,
            `sta`.`has_book_instant_offer` as `has_book_instant_offer`,
            `sta`.`pro_instant_offer_sub_dts` as `pro_instant_offer_sub_dts`,
            `sta`.`book_instant_offer_sub_dts` as `book_instant_offer_sub_dts`,
            `sta`.`exp_unified_id` as `exp_unified_id`,
            toInt32(ifNull(`cr`.`tier`, 0)) as `tier`,
            toInt32(round(ifNull(`sta`.`base_price`, 0))) as `base_price_int`,
            toUInt8(
                (
                    `sta`.`has_pro_instant_offer` > 0
                    and lower(`sta`.`service_name`) like '%pro%'
                    and length(arrayFilter(x -> x between `sta`.`subscribed_dt` and `sta`.`subscribed_dt` + 86400, `sta`.`pro_instant_offer_sub_dts`)) > 0
                )
                or (
                    `sta`.`has_book_instant_offer` > 0
                    and lower(`sta`.`service_name`) like '%book%'
                    and length(arrayFilter(x -> x between `sta`.`subscribed_dt` and `sta`.`subscribed_dt` + 86400, `sta`.`book_instant_offer_sub_dts`)) > 0
                )
            ) as `is_trial2instant`,
            coalesce(`tcm`.`conversion`, `toc`.`conversion`, 0) as `expected_trial_conversion`,
            ifNotFinite(
                ifNull(`sta`.`base_price`, 0) * case
                    when lower(`sta`.`platform`) like '%ios%' then 0.7
                    when lower(`sta`.`platform`) like '%and%' then 0.85
                    else 1
                end,
                0
            ) as `expected_trial_net_price`
        from
            `subscription_matches` as `sta`
        left join
            `default`.`country_regions` as `cr`
        on
            `sta`.`country` = `cr`.`country_code_a2`
        left join
            `latest_trial_conversion_model` as `tcm`
        on
            `sta`.`platform` = `tcm`.`platform`
        and
            toInt32(ifNull(`cr`.`tier`, 0)) = `tcm`.`tier`
        and
            toInt32(round(ifNull(`sta`.`base_price`, 0))) = `tcm`.`base_price_int`
        left join
            `latest_trial_conversion_model` as `toc`
        on
            `sta`.`platform` = `toc`.`platform`
        and
            toInt32(ifNull(`cr`.`tier`, 0)) = `toc`.`tier`
        and
            `toc`.`base_price_int` = -1
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
                `subscription_matches_with_expected` as `sta`
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
    `intros_per_user` as (
        select
            `dt`,
            `variation`,
            ifNotFinite(ifNull(varSamp(`intros_per_user_cnt`), 0), 0) as `intros_per_user_var`
        from (
            select
                toDate(`eut`.`exp_start_dt`) as `dt`,
                `eut`.`variation` as `variation`,
                `eut`.`unified_id` as `unified_id`,
                uniqIf(
                    (`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)),
                    `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800
                    and `sta`.`is_access_intro` = 1
                ) as `intros_per_user_cnt`
            from
                `exp_users` as `eut`
            left join
                `subscription_matches_with_expected` as `sta`
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
                `subscription_matches_with_expected` as `sta`
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
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`) and `sta`.`trial` = 0 and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0) as `access_instant_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`) and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0) as `access_ex_trial_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0) as `access_trial_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`is_access_intro` = 1) as `access_intro_cnt`,
    uniqIf(`eut`.`unified_id`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0) as `trial_subscriber_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` < `eut`.`exp_start_dt` and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`first_charge_expected_dt` > `eut`.`exp_start_dt` and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0 and (`sta`.`charge_dt` = 0 or `sta`.`charge_dt` > `eut`.`exp_start_dt`)) as `active_trial_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`cancel_dt` < `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` > toUnixTimestamp(now()) and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0) as `pending_trial_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`is_otp` = 1) as `access_otp_cnt`,
    `access_instant_cnt` + `access_ex_trial_cnt` + `access_trial_cnt` + `access_intro_cnt` as `subscriptions_cnt`,
    any(`spu`.`subscriptions_per_user_var`) as `subscriptions_per_user_var`,
    any(`ipu`.`intros_per_user_var`) as `intros_per_user_var`,
    `subscriptions_cnt` + `access_otp_cnt` as `access_cnt`,

    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0) as `charged_trial_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`is_trial2instant` = 0 and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0) as `expected_trial_cnt`,
    coalesce(sumIf(`sta`.`expected_trial_conversion`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`is_trial2instant` = 0 and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0), 0) as `expected_charged_trial_cnt`,
    uniqIf(
        (`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)),
        `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`))
        and (
            `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400
            or `sta`.`has_pro_instant_offer` > 0 and lower(`sta`.`service_name`) like '%pro%' and length(arrayFilter(x -> x between `sta`.`subscribed_dt` and `sta`.`subscribed_dt` + 86400, `sta`.`pro_instant_offer_sub_dts`)) > 0
            or `sta`.`has_book_instant_offer` > 0 and lower(`sta`.`service_name`) like '%book%' and length(arrayFilter(x -> x between `sta`.`subscribed_dt` and `sta`.`subscribed_dt` + 86400, `sta`.`book_instant_offer_sub_dts`)) > 0
        ) and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0
    ) as `any_charged_trial_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` < `eut`.`exp_start_dt` and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0) as `active_charged_trial_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and `sta`.`charge_dt` = 0 and `sta`.`cancel_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0) as `cancel_trial_cnt`,
    uniqIf(`eut`.`unified_id`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0) as `trial_buyer_cnt`,
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
    coalesce(sumIf(`sta`.`revenue`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and not (`sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`is_trial2instant` = 0 and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0)), 0)
        - coalesce(sumIf(`sta`.`refund_revenue`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`refund_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600 and not (`sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`is_trial2instant` = 0 and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0)), 0)
        + coalesce(sumIf(`sta`.`expected_trial_net_price` * `sta`.`expected_trial_conversion`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`is_trial2instant` = 0 and `sta`.`is_otp` = 0 and `sta`.`is_access_intro` = 0), 0) as `expected_revenue`,
    coalesce(sumIf(`sta`.`refund_revenue`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`refund_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600), 0) as `refund_revenue`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` < `eut`.`exp_start_dt` and `sta`.`charge_dt` >= `sta`.`subscribed_dt`) as `recurrent_charge_cnt`,
    coalesce(sumIf(`sta`.`revenue`, `sta`.`subscribed_dt` < `eut`.`exp_start_dt` and `sta`.`charge_dt` >= `sta`.`subscribed_dt`), 0) as `recurrent_revenue`,
    coalesce(sumIf(`sta`.`revenue`, `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0), 0) as `trial_revenue`,
    coalesce(sumIf(`sta`.`revenue`, `sta`.`subscribed_dt` < `eut`.`exp_start_dt` and `sta`.`trial` > 0 and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`)) and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0), 0) as `active_trial_revenue`,
    coalesce(sumIf(`sta`.`lifetime_revenue`, `sta`.`subscribed_dt` >= `eut`.`exp_start_dt` and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0), 0)
        - coalesce(sumIf(`sta`.`refund_revenue`, `sta`.`subscribed_dt` >= `eut`.`exp_start_dt` and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0 and `sta`.`refund_dt` >= `sta`.`charge_dt`), 0) as `lifetime_revenue`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`upgrade_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800) as `upgrade_cnt`,
    coalesce(sumIf(`sta`.`upgrade_revenue`, `sta`.`upgrade_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800), 0) as `upgrade_revenue`,
    ifNotFinite(ifNull(varSamp(if(`sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0, `sta`.`revenue`, 0) - if(`sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`refund_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600, `sta`.`refund_revenue`, 0)), 0), 0) as `arpu_var`,
    ifNotFinite(ifNull(varSamp(if(`sta`.`subscribed_dt` >= `eut`.`exp_start_dt` and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0, `sta`.`lifetime_revenue`, 0) - if(`sta`.`subscribed_dt` >= `eut`.`exp_start_dt` and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0 and `sta`.`refund_dt` >= `sta`.`charge_dt`, `sta`.`refund_revenue`, 0)), 0), 0) as `lifetime_arpu_var`,
    ifNotFinite(ifNull(varSampIf(if(`sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0, `sta`.`revenue`, 0) - if(`sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`refund_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600, `sta`.`refund_revenue`, 0), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`is_otp` = 0), 0), 0) as `arppu_var`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`cancel_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600) as `cancel_14d_cnt`,
    uniqIf((`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)), `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + 604800 and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400 and `sta`.`cancel_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 2592000) as `cancel_1m_cnt`
from
    `exp_users` as `eut`
left join
    `subscription_matches_with_expected` as `sta`
on
    `eut`.`unified_id` = `sta`.`exp_unified_id`
left join
    `subscriptions_per_user` as `spu`
on
    toDate(`eut`.`exp_start_dt`) = `spu`.`dt`
and
    `eut`.`variation` = `spu`.`variation`
left join
    `intros_per_user` as `ipu`
on
    toDate(`eut`.`exp_start_dt`) = `ipu`.`dt`
and
    `eut`.`variation` = `ipu`.`variation`
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
