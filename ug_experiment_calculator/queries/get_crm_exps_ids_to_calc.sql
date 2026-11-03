-- CRM (Bloomreach) experiments for the daily calculation: a send is one-off, so
-- an experiment is recalculated while its purchases and 14-day guardrails mature.
select
    distinct `aex`.`id` as `id`
from
    `mysql_u_guitarcom`.`ab_experiment` as `aex`
where
    `aex`.`product` = 'UG'
and
    `aex`.`provider_id` = {provider_id}
and
    `aex`.`archive` = 0
and
    `aex`.`date_start` > 0
and
    `aex`.`date_start` >= toUnixTimestamp(today() - interval {lookback_days} day)
