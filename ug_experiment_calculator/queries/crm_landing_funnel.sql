-- Landing (paywall) funnel of a CRM send: members who opened the CRM landing page from a
-- campaign of this send ('Landing Upgrade Open' with utm_campaign among the send's clicked
-- campaigns) within the attribution window, and the steps they reached afterwards in it.
-- The landing is opened logged in from the email link, so web events carry the recipient's
-- user_id. platform of the first open: 1 = desktop, 2/3 = mobile (the real device, not the
-- _web_/_mob_ segment tag of the UTM).
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
                'Landing Checkout View',
                'Landing Purchase Click',
                'PURCHASE_SUCCESS'
            )
    ),
    `member_events` as (
        select
            `mem`.`user_id` as `user_id`,
            `mem`.`variation` as `variation`,
            `lev`.`event` as `event`,
            `lev`.`datetime` as `datetime`,
            `lev`.`platform` as `platform`,
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
            minIf(`mev`.`datetime`, `mev`.`is_campaign_open` = 1) as `open_dt`,
            argMinIf(`mev`.`platform`, `mev`.`datetime`, `mev`.`is_campaign_open` = 1) as `open_platform`
        from
            `member_events` as `mev`
        group by
            `user_id`
        having
            `open_dt` > toDateTime(0)
    ),
    `member_steps` as (
        select
            `mop`.`user_id` as `user_id`,
            any(`mop`.`variation`) as `variation`,
            any(`mop`.`open_platform`) as `open_platform`,
            toUInt8(countIf(`mev`.`event` = 'Landing Instant Pay Available' and `mev`.`datetime` >= `mop`.`open_dt`) > 0) as `is_instant_pay`,
            toUInt8(countIf(`mev`.`event` = 'Landing Ambulance View' and `mev`.`datetime` >= `mop`.`open_dt`) > 0) as `is_emergency`,
            minIf(`mev`.`datetime`, `mev`.`event` = 'Landing Ambulance View' and `mev`.`datetime` >= `mop`.`open_dt`) as `first_emergency_dt`,
            maxIf(`mev`.`datetime`, `mev`.`event` = 'Landing Checkout View' and `mev`.`datetime` >= `mop`.`open_dt`) as `last_checkout_dt`,
            toUInt8(countIf(`mev`.`event` = 'Landing Checkout View' and `mev`.`datetime` >= `mop`.`open_dt`) > 0) as `is_checkout`,
            toUInt8(countIf(`mev`.`event` = 'Landing Purchase Click' and `mev`.`datetime` >= `mop`.`open_dt`) > 0) as `is_purchase_click`,
            toUInt8(countIf(`mev`.`event` = 'PURCHASE_SUCCESS' and `mev`.`datetime` >= `mop`.`open_dt`) > 0) as `is_access`
        from
            `member_open` as `mop`
        inner join
            `member_events` as `mev`
        on
            `mop`.`user_id` = `mev`.`user_id`
        group by
            `user_id`
    ),
    `member_funnel` as (
        -- the emergency offer has no accept event: "Emergency -> Checkout" is a
        -- Checkout View after the first Ambulance View
        select
            `mst`.`user_id` as `user_id`,
            `mst`.`variation` as `variation`,
            `mst`.`open_platform` as `open_platform`,
            `mst`.`is_instant_pay` as `is_instant_pay`,
            `mst`.`is_emergency` as `is_emergency`,
            toUInt8(`mst`.`is_emergency` = 1 and `mst`.`last_checkout_dt` >= `mst`.`first_emergency_dt`) as `is_emergency_checkout`,
            `mst`.`is_checkout` as `is_checkout`,
            `mst`.`is_purchase_click` as `is_purchase_click`,
            `mst`.`is_access` as `is_access`
        from
            `member_steps` as `mst`
    )
select
    `mfu`.`variation` as `variation`,
    if(`mfu`.`open_platform` = 1, 'desktop', 'mobile') as `platform`,
    count() as `landing_open`,
    sum(`mfu`.`is_instant_pay`) as `instant_pay_available`,
    sum(`mfu`.`is_emergency`) as `emergency_offer_shown`,
    sum(`mfu`.`is_emergency_checkout`) as `emergency_to_checkout`,
    sum(`mfu`.`is_checkout`) as `checkout_view`,
    sum(`mfu`.`is_purchase_click`) as `purchase_click`,
    sum(`mfu`.`is_access`) as `access`
from
    `member_funnel` as `mfu`
group by
    `variation`,
    `platform`
order by
    `variation`,
    `platform`
