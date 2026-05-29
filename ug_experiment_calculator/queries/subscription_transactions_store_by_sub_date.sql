with
    toDate('{date_start}') as date_start,
    toDate('{date_end}') as date_end

select
    `sub`.`subscription_id` as `subscription_id`,
    `sub`.`product_code` as `product_code`,
    any(`sub`.`subscribed_dt`) as `subscribed_dt`,
    minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Charged') as `charge_dt`,
    minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Canceled') as `cancel_dt`,
    minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` = 'Refunded') as `refund_dt`,
    minIf(toUnixTimestamp(`use`.`datetime`), `use`.`event` in ('Upgrade', 'Crossgrade')) as `upgrade_dt`,
    argMinIf(`use`.`usd_price`, `use`.`datetime`, `use`.`event` = 'Charged') as `revenue_gross`,
    argMinIf(
        case
            when `use`.`product_id` in ('com.ultimateguitar.tabs.plus.intro.1year', 'com.ultimateguitar.ugt.plus.intro.1year2', 'com.ultimateguitar.tabs.plus.1year7') then `use`.`usd_price` * 19.99/39.99
            else `use`.`usd_price`
        end,
        `use`.`datetime`, `use`.`event` = 'Refunded'
    ) as `refund_revenue_gross`,
    argMinIf(-toFloat32OrZero(`use`.`params.str_value`[indexOf(`use`.`params.key`, 'usd_refund')]), `use`.`datetime`, `use`.`event` in ('Upgrade', 'Crossgrade')) as `upgrade_revenue`,
    groupArrayIf(
        (
            `use`.`date`,
            case
                when `use`.`product_id` in ('com.ultimateguitar.tabs.plus.intro.1year', 'com.ultimateguitar.ugt.plus.intro.1year2', 'com.ultimateguitar.tabs.plus.1year7') then `use`.`usd_price` * 19.99/39.99
                else `use`.`usd_price`
            end
        ),
        `use`.`event` = 'Charged'
    ) as `all_charges_arr`,
    arrayFilter(
        (t, i) -> i = 1 or t.1 != `all_charges_arr`[i-1].1,
        `all_charges_arr`,
        arrayEnumerate(`all_charges_arr`)
    ) as `all_charges_arr_uniq`,
    now() as `updated_at`
from (
    select
        `subscription_id`,
        `product_code`,
        `subscribed_dt`
    from
        {subscriptions_table}
    where
        toDate(`subscribed_dt`) between date_start and date_end
) as `sub`
left join
    `default`.`ug_subscriptions_events` as `use`
on
    `use`.`subscription_id` = `sub`.`subscription_id`
and
    `use`.`product_code` = `sub`.`product_code`
and
    `use`.`event` in ('Charged', 'Canceled', 'Refunded', 'Crossgrade', 'Upgrade', 'Downgrade')
group by
    `sub`.`subscription_id`,
    `sub`.`product_code`
