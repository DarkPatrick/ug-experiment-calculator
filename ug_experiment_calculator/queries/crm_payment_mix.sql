-- Charged subscriptions of the members by the payment method of their first charge.
select
    `chs`.`variation` as `variation`,
    if(`pme`.`payment_method` = '', 'unknown', `pme`.`payment_method`) as `payment_method`,
    uniqExact(`chs`.`subscription_id`) as `charged_subscriptions`
from (
    select
        `mou`.`variation` as `variation`,
        arrayJoin(`mou`.`charged_subscription_ids`) as `subscription_id`
    from
        `member_outcomes` as `mou`
    where
        notEmpty(`mou`.`charged_subscription_ids`)
) as `chs`
left join (
    select
        `use`.`subscription_id` as `subscription_id`,
        argMin(toString(`use`.`payment_method`), `use`.`datetime`) as `payment_method`
    from
        `default`.`ug_subscriptions_events` as `use`
    where
        `use`.`event` = 'Charged'
    and
        `use`.`date` between toDate('{date_start}') and toDate('{date_end}') + interval {attribution_window_days} day
    group by
        `subscription_id`
) as `pme`
on
    `chs`.`subscription_id` = `pme`.`subscription_id`
group by
    `variation`,
    `payment_method`
order by
    `variation`,
    `charged_subscriptions` desc
