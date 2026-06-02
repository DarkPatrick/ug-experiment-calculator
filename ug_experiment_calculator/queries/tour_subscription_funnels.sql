with
    604800 as `observation_window`,
    `funnel_counts` as (
        select
            `dt`,
            `variation`,
            uniqIf(`unified_id`, `has_no_active_pro_rights`) as `x1_users`,
            uniqIf(`unified_id`, `has_no_active_pro_rights` and `tour_install_pro_trial_dt` > 0) as `x2_users`,
            uniqIf(
                `unified_id`,
                `has_no_active_pro_rights`
                and `tour_install_pro_trial_dt` > 0
                and length(arrayFilter(
                    x -> x between `tour_install_pro_trial_dt` and `tour_install_pro_trial_dt` + 86400,
                    `tour_instant_offer_charge_dts`
                )) > 0
            ) as `x3_users`,
            uniqIf(
                `unified_id`,
                `has_no_active_pro_rights`
                and `has_tour_install_subscription` = 0
            ) as `x4_users`,
            uniqIf(
                `unified_id`,
                `has_no_active_pro_rights`
                and `has_tour_install_subscription` = 0
                and `tour_post_decline_instant_offer_dt` > 0
            ) as `x5_users`
        from (
            select
                `eut`.`dt` as `dt`,
                `eut`.`variation` as `variation`,
                `eut`.`unified_id` as `unified_id`,
                `eut`.`has_no_active_pro_rights` as `has_no_active_pro_rights`,
                minIf(
                    `sta`.`subscribed_dt`,
                    `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + `observation_window`
                    and lower(`sta`.`funnel_source`) = 'tour install'
                    and `sta`.`trial` > 0
                    and not (toDate(`sta`.`charge_dt`) = toDate(`sta`.`subscribed_dt`))
                    and `sta`.`is_otp` = 0
                    and lower(`sta`.`service_name`) like '%pro%'
                ) as `tour_install_pro_trial_dt`,
                groupArrayIf(
                    `sta`.`subscribed_dt`,
                    `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + `observation_window`
                    and lower(`sta`.`funnel_source`) = 'tour instant offer'
                    and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400
                    and `sta`.`is_otp` = 0
                ) as `tour_instant_offer_charge_dts`,
                max(
                    if(
                        `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + `observation_window`
                        and lower(`sta`.`funnel_source`) = 'tour install',
                        1,
                        0
                    )
                ) as `has_tour_install_subscription`,
                minIf(
                    `sta`.`subscribed_dt`,
                    `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + `observation_window`
                    and lower(`sta`.`funnel_source`) = 'tour post decline instant offer'
                    and `sta`.`is_otp` = 0
                ) as `tour_post_decline_instant_offer_dt`
            from (
                select distinct
                    toDate(`exp_start_dt`) as `dt`,
                    `exp_start_dt`,
                    `variation`,
                    `unified_id`,
                    toUInt32(`rights` / 1) % 10 in (0, 4, 5) as `has_no_active_pro_rights`
                from
                    {exp_users_table}
                where
                    `client` = {client_sql}
                and
                    `segment` = {segment_sql}
            ) as `eut`
            left join (
                select distinct *
                from {subscription_table}
            ) as `sta`
            on
                `eut`.`unified_id` = `sta`.`unified_id`
            group by
                `dt`,
                `variation`,
                `unified_id`,
                `has_no_active_pro_rights`
        )
        group by
            `dt`,
            `variation`
    )

select
    `dt`,
    `variation`,
    'tour_pro_trial_instant_offer_charge' as `funnel_key`,
    'Tour Pro trial -> instant offer charge' as `funnel_name`,
    'x1_to_x2' as `transition_key`,
    'x1 -> x2' as `transition_name`,
    'x1' as `from_step_key`,
    'No active Pro rights' as `from_step_name`,
    toUInt8(1) as `from_step_order`,
    'x2' as `to_step_key`,
    'Tour Install Pro trial' as `to_step_name`,
    toUInt8(2) as `to_step_order`,
    `x1_users` as `denominator_users`,
    `x2_users` as `numerator_users`
from
    `funnel_counts`

union all

select
    `dt`,
    `variation`,
    'tour_pro_trial_instant_offer_charge' as `funnel_key`,
    'Tour Pro trial -> instant offer charge' as `funnel_name`,
    'x2_to_x3' as `transition_key`,
    'x2 -> x3' as `transition_name`,
    'x2' as `from_step_key`,
    'Tour Install Pro trial' as `from_step_name`,
    toUInt8(2) as `from_step_order`,
    'x3' as `to_step_key`,
    'Charged Tour Instant Offer subscription' as `to_step_name`,
    toUInt8(3) as `to_step_order`,
    `x2_users` as `denominator_users`,
    `x3_users` as `numerator_users`
from
    `funnel_counts`

union all

select
    `dt`,
    `variation`,
    'tour_post_decline_instant_offer' as `funnel_key`,
    'No Tour Install subscription -> post-decline instant offer' as `funnel_name`,
    'x4_to_x5' as `transition_key`,
    'x4 -> x5' as `transition_name`,
    'x4' as `from_step_key`,
    'No active Pro rights and no Tour Install subscription' as `from_step_name`,
    toUInt8(1) as `from_step_order`,
    'x5' as `to_step_key`,
    'Tour Post Decline Instant Offer subscription' as `to_step_name`,
    toUInt8(2) as `to_step_order`,
    `x4_users` as `denominator_users`,
    `x5_users` as `numerator_users`
from
    `funnel_counts`
order by
    `dt`,
    `variation`,
    `funnel_key`,
    `from_step_order`,
    `to_step_order`
