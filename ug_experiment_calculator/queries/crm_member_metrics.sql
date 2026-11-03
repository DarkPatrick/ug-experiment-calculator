-- Per-variation sums for the CRM ratio metrics (revenue per 1000 delivered,
-- clicks -> charges): first and second moments of each numerator / denominator
-- pair, so the delta-method variance of the ratio can be built in Python.
select
    `mou`.`variation` as `variation`,
    count() as `members`,
    sum(`efl`.`is_delivered`) as `delivered_cnt`,
    sum(`efl`.`is_clicked`) as `clicked_cnt`,
    sum(`mou`.`is_buyer`) as `buyer_cnt`,
    sum(`mou`.`charge_cnt`) as `charge_cnt`,
    sum(`mou`.`net_revenue`) as `revenue`,
    sum(`mou`.`net_revenue` * `mou`.`net_revenue`) as `revenue_sq`,
    sum(`mou`.`net_revenue` * `efl`.`is_delivered`) as `revenue_x_delivered`,
    sum(`mou`.`charge_cnt` * `mou`.`charge_cnt`) as `charge_sq`,
    sum(`mou`.`charge_cnt` * `efl`.`is_clicked`) as `charge_x_clicked`
from
    `member_outcomes` as `mou`
left join
    `email_flags` as `efl`
on
    `mou`.`unified_id` = `efl`.`unified_id`
group by
    `variation`
order by
    `variation`
