with
    `exp_users` as (
        select distinct
            `unified_id`,
            `variation`,
            toDate(toDateTime(`exp_start_dt`, 'UTC')) as `dt`,
            toDateTime(`exp_start_dt`, 'UTC') as `exp_start_datetime`,
            {app_retention_unified_id_sql} as `app_retention_unified_id`
        from {exp_users_table}
        where
            `client` = {client_sql}
        and
            `segment` = {segment_sql}
        and
            `segment_hash` = {segment_hash_sql}
    ),
    `date_bounds` as (
        select
            min(`dt`) as `min_dt`,
            max(`dt`) + interval 15 day as `max_retention_dt`
        from
            `exp_users`
    ),
    `web_retention` as (
        select
            `eut`.`dt` as `dt`,
            `eut`.`variation` as `variation`,
            uniqExactIf(`eut`.`unified_id`, `urew`.`datetime` between `eut`.`exp_start_datetime` + interval 24 hour and `eut`.`exp_start_datetime` + interval 48 hour) as `web_retention_1d_cnt`,
            uniqExactIf(`eut`.`unified_id`, `urew`.`datetime` between `eut`.`exp_start_datetime` + interval 24 hour and `eut`.`exp_start_datetime` + interval 192 hour) as `web_retention_7d_cnt`,
            uniqExactIf(`eut`.`unified_id`, `urew`.`datetime` between `eut`.`exp_start_datetime` + interval 24 hour and `eut`.`exp_start_datetime` + interval 360 hour) as `web_retention_14d_cnt`
        from
            `exp_users` as `eut`
        inner join
            `default`.`ug_rt_events_web` as `urew`
        on
            `urew`.`unified_id` = `eut`.`unified_id`
        where
            {calculate_web_retention_sql}
        and
            `urew`.`date` between (select `min_dt` from `date_bounds`) and (select `max_retention_dt` from `date_bounds`)
        and
            `urew`.`event` in ('Tab View', 'Home View')
        group by
            `dt`,
            `variation`
    ),
    `app_retention` as (
        select
            `eut`.`dt` as `dt`,
            `eut`.`variation` as `variation`,
            uniqExactIf(`eut`.`unified_id`, `urea`.`datetime` between `eut`.`exp_start_datetime` + interval 24 hour and `eut`.`exp_start_datetime` + interval 48 hour) as `app_retention_1d_cnt`,
            uniqExactIf(`eut`.`unified_id`, `urea`.`datetime` between `eut`.`exp_start_datetime` + interval 24 hour and `eut`.`exp_start_datetime` + interval 192 hour) as `app_retention_7d_cnt`,
            uniqExactIf(`eut`.`unified_id`, `urea`.`datetime` between `eut`.`exp_start_datetime` + interval 24 hour and `eut`.`exp_start_datetime` + interval 360 hour) as `app_retention_14d_cnt`
        from
            `exp_users` as `eut`
        inner join
            `default`.`ug_rt_events_app` as `urea`
        on
            `urea`.`unified_id` = `eut`.`app_retention_unified_id`
        where
            {calculate_app_retention_sql}
        and
            `eut`.`app_retention_unified_id` > 0
        and
            `urea`.`date` between (select `min_dt` from `date_bounds`) and (select `max_retention_dt` from `date_bounds`)
        and
            `urea`.`event` in ('Tab Open', 'App Start', 'Courses Open', 'Shots Open', 'Tabs Open')
        group by
            `dt`,
            `variation`
    )

select
    `days`.`dt` as `dt`,
    `days`.`variation` as `variation`,
    toUInt64(ifNull(`wr`.`web_retention_1d_cnt`, 0)) as `web_retention_1d_cnt`,
    toUInt64(ifNull(`wr`.`web_retention_7d_cnt`, 0)) as `web_retention_7d_cnt`,
    toUInt64(ifNull(`wr`.`web_retention_14d_cnt`, 0)) as `web_retention_14d_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`ar`.`app_retention_1d_cnt`, 0))) as `app_retention_1d_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`ar`.`app_retention_7d_cnt`, 0))) as `app_retention_7d_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`ar`.`app_retention_14d_cnt`, 0))) as `app_retention_14d_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`ar`.`app_retention_1d_cnt`, 0), 0)) as `mobweb_app_retention_1d_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`ar`.`app_retention_7d_cnt`, 0), 0)) as `mobweb_app_retention_7d_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`ar`.`app_retention_14d_cnt`, 0), 0)) as `mobweb_app_retention_14d_cnt`
from
    (
        select distinct
            `dt`,
            `variation`
        from
            `exp_users`
    ) as `days`
left join
    `web_retention` as `wr`
on
    `days`.`dt` = `wr`.`dt`
and
    `days`.`variation` = `wr`.`variation`
left join
    `app_retention` as `ar`
on
    `days`.`dt` = `ar`.`dt`
and
    `days`.`variation` = `ar`.`variation`
order by
    `dt`,
    `variation`
