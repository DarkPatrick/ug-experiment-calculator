with
    toDate('{date_start}') as date_start,
    toDate('{date_end}') as date_end

select
    `use`.`subscription_id` as `subscription_id`,
    `use`.`product_code` as `product_code`,
    min(toUnixTimestamp(`use`.`datetime`)) as `subscribed_dt`,
    argMin(`use`.`platform`, `use`.`datetime`) as `platform`,
    argMin(
        case
            when `use`.`datetime_next_billing` < `use`.`datetime` then toUnixTimestamp(`use`.`datetime`)
            else toUnixTimestamp(`use`.`datetime_next_billing`)
        end,
        `use`.`datetime`
    ) as `first_charge_expected_dt`,
    argMin(`use`.`trial`, `use`.`datetime`) as `trial`,
    argMin(`use`.`funnel_source`, `use`.`datetime`) as `funnel_source`,
    argMin(`use`.`product_id`, `use`.`datetime`) as `product_id`,
    argMin(`use`.`user_id`, `use`.`datetime`) as `user_id`,
    argMin(`use`.`unified_id`, `use`.`datetime`) as `unified_id`,
    argMin(`use`.`payment_account_id`, `use`.`datetime`) as `payment_account_id`,
    argMin(`use`.`service_name`, `use`.`datetime`) as `service_name`,
    argMin(`use`.`duration_count`, `use`.`datetime`) as `duration_count`,
    if (
        (`duration_count` = 0 and `service_name` = '' and `trial` = 0)
            or (`product_id` like 'onetime%' or `product_id` like '%|paid_trial')
        , 1, 0
    ) as `is_otp`,
    now() as `updated_at`
from
    `default`.`ug_subscriptions_events` as `use`
where
    `use`.`event` = 'Subscribed'
group by
    `subscription_id`,
    `product_code`
having
    toDate(`subscribed_dt`) between date_start and date_end
