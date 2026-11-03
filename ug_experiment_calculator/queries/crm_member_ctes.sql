    -- Appended to the CTEs of monetization_metrics.sql (rendered with this experiment's
    -- parameters), so every member's purchases come from the calculator's own matching:
    -- the same subscriptions, window, charge rule and 14-day refunds as the stats table.
    `member_outcomes` as (
        select
            `eut`.`unified_id` as `unified_id`,
            any(`eut`.`user_id`) as `user_id`,
            any(`eut`.`variation`) as `variation`,
            any(`eut`.`exp_start_dt`) as `exp_start_dt`,
            toUInt8(countIf(
                `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + {attribution_window_seconds}
                and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400
            ) > 0) as `is_buyer`,
            uniqExactIf(
                (`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)),
                `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + {attribution_window_seconds}
                and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400
                and `sta`.`is_otp` = 0
            ) + uniqExactIf(
                (`sta`.`subscription_id`, `sta`.`product_id`, toDate(`sta`.`subscribed_dt`)),
                `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + {attribution_window_seconds}
                and `sta`.`is_otp` = 1
            ) as `charge_cnt`,
            coalesce(sumIf(
                `sta`.`revenue`,
                `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + {attribution_window_seconds}
                and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400
            ), 0) - coalesce(sumIf(
                `sta`.`refund_revenue`,
                `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + {attribution_window_seconds}
                and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400
                and `sta`.`refund_dt` between `sta`.`charge_dt` and `sta`.`charge_dt` + 1209600
            ), 0) as `net_revenue`,
            groupUniqArrayIf(
                `sta`.`subscription_id`,
                `sta`.`subscribed_dt` between `eut`.`exp_start_dt` and `eut`.`exp_start_dt` + {attribution_window_seconds}
                and `sta`.`charge_dt` between `sta`.`subscribed_dt` and `sta`.`first_charge_expected_dt` + 86400
            ) as `charged_subscription_ids`
        from
            `exp_users` as `eut`
        left join
            `subscription_matches_with_expected` as `sta`
        on
            `eut`.`unified_id` = `sta`.`exp_unified_id`
        group by
            `unified_id`
    ),
    `email_events` as (
        select
            `bce`.`user_id` as `user_id`,
            `bce`.`datetime` as `datetime`,
            `bce`.`event` as `event`
        from
            `monetisation_crm`.`ug_bloomreach_campaign_events` as `bce`
        where
            `bce`.`date` between toDate('{date_start}') and toDate('{date_end}') + interval {attribution_window_days} day
        and
            `bce`.`bm_campaign_id` = {provider_experiment_id_sql}
        and
            `bce`.`event` in ('Email Delivered', 'Email Clicked')
        and (
            `bce`.`event` != 'Email Clicked'
            or countMatches(`bce`.`bm_campaign_url`, 'consent') = 0
        )
    ),
    `email_flags` as (
        -- same recipient-level flags as crm_email_metrics.sql
        select
            `mou`.`unified_id` as `unified_id`,
            toUInt8(countIf(`eme`.`event` = 'Email Delivered' and `eme`.`datetime` between toDateTime(`mou`.`exp_start_dt`) and toDateTime(`mou`.`exp_start_dt`) + {attribution_window_seconds}) > 0) as `is_delivered`,
            toUInt8(countIf(`eme`.`event` = 'Email Clicked' and `eme`.`datetime` between toDateTime(`mou`.`exp_start_dt`) and toDateTime(`mou`.`exp_start_dt`) + {attribution_window_seconds}) > 0) as `is_clicked`
        from
            `member_outcomes` as `mou`
        left join
            `email_events` as `eme`
        on
            `mou`.`user_id` = `eme`.`user_id`
        group by
            `unified_id`
    )
