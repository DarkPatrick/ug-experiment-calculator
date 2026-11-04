-- Step-by-step landing (paywall) funnel of a CRM send, in the layout of the CRM page template
-- (Confluence "UG AI Analytics & CRM"): at each step count who bought there, and only those who did
-- not buy go on. A visitor = a member who opened the CRM landing ('Landing Upgrade Open') from a
-- campaign of this send within the attribution window. The landing is opened logged in from the
-- email link, so web events carry the recipient's user_id.
--   * Where a purchase happened = `step` of the last 'Landing Checkout View' before the first
--     PURCHASE_SUCCESS: 'paywall' = the payment sheet was opened by a button on the paywall
--     itself (no checkout page), anything else = checkout.
--   * A purchase before the first 'Landing Ambulance View' counts for the main offer, after it for
--     the emergency offer (the offer has no accept event).
--   * Wallet = the payment method of the member's first charge in the window
--     (ug_subscriptions_events.payment_method: Apple Pay / Google Pay).
--   * platform of the first open: 1 = desktop, 2/3 = mobile (the real device).
with
    `members` as (
        select
            `eut`.`user_id` as `user_id`,
            any(`eut`.`variation`) as `variation`,
            min(`eut`.`exp_start_dt`) as `exp_start_dt`
        from
            {exp_users_table} as `eut`
        where
            `eut`.`client` = {client_sql}
        and
            `eut`.`segment` = {segment_sql}
        and
            `eut`.`segment_hash` = {segment_hash_sql}
        group by
            `user_id`
    ),
    `landing_events` as (
        select
            `urew`.`user_id` as `user_id`,
            `urew`.`event` as `event`,
            `urew`.`datetime` as `datetime`,
            toInt64OrZero(toString(`urew`.`platform`)) as `platform`,
            `urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'step')] as `step`,
            toUInt8(`urew`.`event` = 'Landing Upgrade Open' and lower(extractURLParameter(`urew`.`url`, 'utm_campaign')) in ({utm_campaigns_sql})) as `is_campaign_open`
        from
            `default`.`ug_rt_events_web` as `urew`
        where
            `urew`.`date` between toDate('{date_start}') and toDate('{date_end}') + interval {attribution_window_days} day
        and
            `urew`.`user_id` > 0
        and
            `urew`.`event` in (
                'Landing Upgrade Open',
                'Landing Instant Pay Available',
                'Landing Ambulance View',
                'Landing Ambulance Close',
                'Landing Checkout View',
                'PURCHASE_SUCCESS'
            )
    ),
    `member_events` as (
        select
            `mem`.`user_id` as `user_id`,
            `mem`.`variation` as `variation`,
            `mem`.`exp_start_dt` as `exp_start_dt`,
            `lev`.`event` as `event`,
            `lev`.`datetime` as `datetime`,
            `lev`.`platform` as `platform`,
            `lev`.`step` as `step`,
            `lev`.`is_campaign_open` as `is_campaign_open`
        from
            `members` as `mem`
        inner join
            `landing_events` as `lev`
        on
            `mem`.`user_id` = `lev`.`user_id`
        where
            `lev`.`datetime` between toDateTime(`mem`.`exp_start_dt`) and toDateTime(`mem`.`exp_start_dt`) + {attribution_window_seconds}
    ),
    `member_open` as (
        select
            `mev`.`user_id` as `user_id`,
            any(`mev`.`variation`) as `variation`,
            any(`mev`.`exp_start_dt`) as `exp_start_dt`,
            minIf(`mev`.`datetime`, `mev`.`is_campaign_open` = 1) as `open_dt`,
            argMinIf(`mev`.`platform`, `mev`.`datetime`, `mev`.`is_campaign_open` = 1) as `open_platform`
        from
            `member_events` as `mev`
        group by
            `user_id`
        having
            `open_dt` > toDateTime(0)
    ),
    `member_marks` as (
        select
            `mop`.`user_id` as `user_id`,
            any(`mop`.`variation`) as `variation`,
            any(`mop`.`exp_start_dt`) as `exp_start_dt`,
            any(`mop`.`open_dt`) as `open_dt`,
            any(`mop`.`open_platform`) as `open_platform`,
            minIf(`mev`.`datetime`, `mev`.`event` = 'Landing Ambulance View' and `mev`.`datetime` >= `mop`.`open_dt`) as `first_emergency_dt`,
            minIf(`mev`.`datetime`, `mev`.`event` = 'PURCHASE_SUCCESS' and `mev`.`datetime` >= `mop`.`open_dt`) as `first_buy_dt`
        from
            `member_open` as `mop`
        inner join
            `member_events` as `mev`
        on
            `mop`.`user_id` = `mev`.`user_id`
        group by
            `user_id`
    ),
    `member_marks_flags` as (
        select
            `mma`.`user_id` as `user_id`,
            `mma`.`variation` as `variation`,
            `mma`.`exp_start_dt` as `exp_start_dt`,
            `mma`.`open_dt` as `open_dt`,
            `mma`.`open_platform` as `open_platform`,
            `mma`.`first_emergency_dt` as `first_emergency_dt`,
            `mma`.`first_buy_dt` as `first_buy_dt`,
            toUInt8(`mma`.`first_emergency_dt` > toDateTime(0)) as `has_emergency`,
            toUInt8(`mma`.`first_buy_dt` > toDateTime(0)) as `is_bought`,
            toUInt8(
                `mma`.`first_buy_dt` > toDateTime(0)
                and (`mma`.`first_emergency_dt` = toDateTime(0) or `mma`.`first_buy_dt` < `mma`.`first_emergency_dt`)
            ) as `is_bought_main`
        from
            `member_marks` as `mma`
    ),
    `member_step_events` as (
        select
            `mmf`.`user_id` as `user_id`,
            any(`mmf`.`variation`) as `variation`,
            any(`mmf`.`open_platform`) as `open_platform`,
            any(`mmf`.`has_emergency`) as `has_emergency`,
            any(`mmf`.`is_bought`) as `is_bought`,
            any(`mmf`.`is_bought_main`) as `is_bought_main`,
            argMaxIf(
                `mev`.`step`,
                `mev`.`datetime`,
                `mev`.`event` = 'Landing Checkout View' and `mev`.`datetime` >= `mmf`.`open_dt` and `mev`.`datetime` <= `mmf`.`first_buy_dt`
            ) as `buy_step`,
            toUInt8(countIf(`mev`.`event` = 'Landing Instant Pay Available' and `mev`.`datetime` >= `mmf`.`open_dt`) > 0) as `is_instant_pay`,
            toUInt8(countIf(
                `mev`.`event` = 'Landing Checkout View' and `mev`.`step` = 'paywall' and `mev`.`datetime` >= `mmf`.`open_dt`
                and (`mmf`.`has_emergency` = 0 or `mev`.`datetime` < `mmf`.`first_emergency_dt`)
            ) > 0) as `is_wallet_tap_main`,
            toUInt8(countIf(
                `mev`.`event` = 'Landing Checkout View' and `mev`.`step` != 'paywall' and `mev`.`datetime` >= `mmf`.`open_dt`
                and (`mmf`.`has_emergency` = 0 or `mev`.`datetime` < `mmf`.`first_emergency_dt`)
            ) > 0) as `is_checkout_main`,
            toUInt8(countIf(`mev`.`event` = 'Landing Ambulance Close' and `mmf`.`has_emergency` = 1 and `mev`.`datetime` >= `mmf`.`first_emergency_dt`) > 0) as `is_emergency_closed`,
            toUInt8(countIf(
                `mev`.`event` = 'Landing Checkout View' and `mev`.`step` = 'paywall'
                and `mmf`.`has_emergency` = 1 and `mev`.`datetime` >= `mmf`.`first_emergency_dt`
            ) > 0) as `is_wallet_tap_emergency`,
            toUInt8(countIf(
                `mev`.`event` = 'Landing Checkout View' and `mev`.`step` != 'paywall'
                and `mmf`.`has_emergency` = 1 and `mev`.`datetime` >= `mmf`.`first_emergency_dt`
            ) > 0) as `is_checkout_emergency`
        from
            `member_marks_flags` as `mmf`
        inner join
            `member_events` as `mev`
        on
            `mmf`.`user_id` = `mev`.`user_id`
        group by
            `user_id`
    ),
    `member_charges` as (
        select
            `mmf`.`user_id` as `user_id`,
            argMin(toString(`use`.`payment_method`), `use`.`datetime`) as `payment_method`
        from
            `member_marks_flags` as `mmf`
        inner join
            `default`.`ug_subscriptions_events` as `use`
        on
            `mmf`.`user_id` = `use`.`user_id`
        where
            `use`.`event` = 'Charged'
        and
            `use`.`date` between toDate('{date_start}') and toDate('{date_end}') + interval {attribution_window_days} day
        and
            `mmf`.`is_bought` = 1
        and
            `use`.`datetime` >= `mmf`.`open_dt`
        and
            `use`.`datetime` <= toDateTime(`mmf`.`exp_start_dt`) + {attribution_window_seconds}
        group by
            `user_id`
    ),
    `member_funnel` as (
        select
            `mse`.`variation` as `variation`,
            if(`mse`.`open_platform` = 1, 'desktop', 'mobile') as `platform`,
            `mse`.`is_instant_pay` as `is_instant_pay`,
            `mse`.`is_wallet_tap_main` as `is_wallet_tap_main`,
            toUInt8(`mse`.`is_bought_main` = 1 and `mse`.`buy_step` = 'paywall') as `is_bought_paywall`,
            toUInt8(`mse`.`is_bought_main` = 1 and `mse`.`buy_step` != 'paywall') as `is_bought_checkout`,
            toUInt8(
                not (`mse`.`is_bought_main` = 1 and `mse`.`buy_step` = 'paywall')
                and (`mse`.`is_checkout_main` = 1 or (`mse`.`is_bought_main` = 1 and `mse`.`buy_step` != 'paywall'))
            ) as `is_went_checkout`,
            `mse`.`has_emergency` as `has_emergency`,
            `mse`.`is_emergency_closed` as `is_emergency_closed`,
            `mse`.`is_wallet_tap_emergency` as `is_wallet_tap_emergency`,
            `mse`.`is_checkout_emergency` as `is_checkout_emergency`,
            `mse`.`is_bought` as `is_bought`,
            `mse`.`is_bought_main` as `is_bought_main`,
            toUInt8(`mse`.`is_bought` = 1 and `mse`.`is_bought_main` = 0) as `is_bought_emergency`,
            toUInt8(`mse`.`is_bought` = 1 and `mse`.`is_bought_main` = 0 and `mse`.`buy_step` = 'paywall') as `is_bought_emergency_offer`,
            toUInt8(`mcr`.`payment_method` in ('Apple Pay', 'Google Pay')) as `is_wallet_paid`
        from
            `member_step_events` as `mse`
        left join
            `member_charges` as `mcr`
        on
            `mse`.`user_id` = `mcr`.`user_id`
    ),
    `member_paths` as (
        select
            `mfu`.`variation` as `variation`,
            `mfu`.`platform` as `platform`,
            `mfu`.`is_instant_pay` as `is_instant_pay`,
            `mfu`.`is_wallet_tap_main` as `is_wallet_tap_main`,
            `mfu`.`is_bought_paywall` as `is_bought_paywall`,
            `mfu`.`is_bought_checkout` as `is_bought_checkout`,
            `mfu`.`is_went_checkout` as `is_went_checkout`,
            `mfu`.`has_emergency` as `has_emergency`,
            `mfu`.`is_emergency_closed` as `is_emergency_closed`,
            `mfu`.`is_wallet_tap_emergency` as `is_wallet_tap_emergency`,
            `mfu`.`is_checkout_emergency` as `is_checkout_emergency`,
            `mfu`.`is_bought` as `is_bought`,
            `mfu`.`is_bought_main` as `is_bought_main`,
            `mfu`.`is_bought_emergency` as `is_bought_emergency`,
            `mfu`.`is_bought_emergency_offer` as `is_bought_emergency_offer`,
            `mfu`.`is_wallet_paid` as `is_wallet_paid`,
            toUInt8(`mfu`.`is_went_checkout` = 1 and `mfu`.`is_bought_checkout` = 0) as `is_left_checkout`,
            toUInt8(`mfu`.`is_bought_paywall` = 0 and `mfu`.`is_went_checkout` = 0) as `is_left_paywall`
        from
            `member_funnel` as `mfu`
    )
select
    `mpa`.`variation` as `variation`,
    `mpa`.`platform` as `platform`,
    count() as `opened_paywall`,
    sum(`mpa`.`is_instant_pay`) as `instant_pay_available`,
    sum(`mpa`.`is_wallet_tap_main`) as `wallet_tap_paywall`,
    sum(`mpa`.`is_bought_paywall`) as `bought_paywall`,
    sum(`mpa`.`is_bought_paywall` * `mpa`.`is_wallet_paid`) as `bought_paywall_wallet`,
    sum(`mpa`.`is_went_checkout`) as `went_checkout`,
    sum(`mpa`.`is_bought_checkout`) as `bought_checkout`,
    sum(`mpa`.`is_bought_checkout` * `mpa`.`is_wallet_paid`) as `bought_checkout_wallet`,
    sum(`mpa`.`is_left_checkout`) as `left_checkout`,
    sum(`mpa`.`is_left_paywall`) as `left_paywall`,
    sum(`mpa`.`is_left_checkout` + `mpa`.`is_left_paywall`) as `not_bought_main`,
    sum(`mpa`.`has_emergency` * (`mpa`.`is_left_checkout` + `mpa`.`is_left_paywall`)) as `saw_emergency`,
    sum(`mpa`.`has_emergency` * `mpa`.`is_left_checkout`) as `saw_emergency_left_checkout`,
    sum(`mpa`.`has_emergency` * `mpa`.`is_left_paywall`) as `saw_emergency_left_paywall`,
    sum(`mpa`.`has_emergency` * (`mpa`.`is_left_checkout` + `mpa`.`is_left_paywall`) * `mpa`.`is_emergency_closed`) as `emergency_closed`,
    sum(`mpa`.`has_emergency` * (`mpa`.`is_left_checkout` + `mpa`.`is_left_paywall`) * `mpa`.`is_wallet_tap_emergency`) as `wallet_tap_emergency`,
    sum(`mpa`.`has_emergency` * (`mpa`.`is_left_checkout` + `mpa`.`is_left_paywall`) * `mpa`.`is_checkout_emergency`) as `emergency_to_checkout`,
    sum(`mpa`.`is_bought_emergency`) as `bought_emergency`,
    sum(`mpa`.`is_bought_emergency_offer`) as `bought_emergency_offer`,
    sum(`mpa`.`is_bought_emergency` - `mpa`.`is_bought_emergency_offer`) as `bought_emergency_checkout`,
    sum(`mpa`.`is_bought_emergency` * `mpa`.`is_left_checkout`) as `bought_emergency_left_checkout`,
    sum(`mpa`.`is_bought_emergency` * `mpa`.`is_left_paywall`) as `bought_emergency_left_paywall`,
    sum(1 - `mpa`.`is_bought`) as `bought_nothing`,
    sum(`mpa`.`is_bought`) as `total_bought`,
    sum(`mpa`.`is_bought` * `mpa`.`is_wallet_paid`) as `total_bought_wallet`
from
    `member_paths` as `mpa`
group by
    `variation`,
    `platform`
order by
    `variation`,
    `platform`
