with
    (
        select
            if(`aee`.`date_end` < `aee`.`date_start`, toDateTime(now()), toDateTime(`aee`.`date_end`))
        from
            `mysql_u_guitarcom`.`ab_experiment_export` as `aee`
        where
            `aee`.`product` = 'UG'
        and
            `aee`.`id` = {exp_id}
        limit 1
    ) as `exp_end_datetime`,
    toDate(`exp_end_datetime`) as `exp_end_date`,
    `exp_users` as (
        select distinct
            `unified_id`,
            `variation`,
            toDate(toDateTime(`exp_start_dt`, 'UTC')) as `dt`,
            toDateTime(`exp_start_dt`, 'UTC') as `exp_start_datetime`,
            {app_tab_view_unified_id_sql} as `app_tab_view_unified_id`
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
            min(`dt`) as `min_dt`
        from
            `exp_users`
    ),
    `web_event_counts` as (
        select
            `eut`.`dt` as `dt`,
            `eut`.`variation` as `variation`,
            `eut`.`unified_id` as `unified_id`,
            uniqExactIf(`urew`.`datetime`, `urew`.`event` = 'Tab View') as `tab_view_events_cnt`,
            uniqExactIf(`urew`.`datetime`, `urew`.`event` = 'Tab View 60s') as `tab_view_60s_events_cnt`,
            uniqExactIf(`urew`.`datetime`, `urew`.`event` = 'Tab View 120s') as `tab_view_120s_events_cnt`,
            uniqExactIf(`urew`.`datetime`, `urew`.`event` = 'Tab View 180s') as `tab_view_180s_events_cnt`,
            uniqExactIf(`urew`.`datetime`, `urew`.`event` = 'Tab View 300s') as `tab_view_300s_events_cnt`,
            uniqExactIf(`urew`.`datetime`, `urew`.`event` = 'Tab View 600s') as `tab_view_600s_events_cnt`
        from
            (
                select
                    `unified_id`,
                    `event`,
                    `datetime`
                from
                    `default`.`ug_rt_events_web`
                where
                    {calculate_web_tab_view_sql}
                and
                    `date` between (select `min_dt` from `date_bounds`) and `exp_end_date`
                and
                    `event` in ('Tab View', 'Tab View 60s', 'Tab View 120s', 'Tab View 180s', 'Tab View 300s', 'Tab View 600s')
            ) as `urew`
        inner join
            `exp_users` as `eut`
        on
            `urew`.`unified_id` = `eut`.`unified_id`
        where
            `urew`.`datetime` between `eut`.`exp_start_datetime` and `exp_end_datetime`
        group by
            `dt`,
            `variation`,
            `unified_id`
    ),
    `app_event_counts` as (
        select
            `eut`.`dt` as `dt`,
            `eut`.`variation` as `variation`,
            `eut`.`unified_id` as `unified_id`,
            uniqExactIf(`urea`.`datetime`, `urea`.`event` = 'Tab View') as `tab_view_events_cnt`,
            uniqExactIf(`urea`.`datetime`, `urea`.`event` = 'Tab View 60s') as `tab_view_60s_events_cnt`,
            uniqExactIf(`urea`.`datetime`, `urea`.`event` = 'Tab View 120s') as `tab_view_120s_events_cnt`,
            uniqExactIf(`urea`.`datetime`, `urea`.`event` = 'Tab View 180s') as `tab_view_180s_events_cnt`,
            uniqExactIf(`urea`.`datetime`, `urea`.`event` = 'Tab View 300s') as `tab_view_300s_events_cnt`,
            uniqExactIf(`urea`.`datetime`, `urea`.`event` = 'Tab View 600s') as `tab_view_600s_events_cnt`
        from
            (
                select
                    `unified_id`,
                    `event`,
                    `datetime`
                from
                    `default`.`ug_rt_events_app`
                where
                    {calculate_app_tab_view_sql}
                and
                    `date` between (select `min_dt` from `date_bounds`) and `exp_end_date`
                and
                    `event` in ('Tab View', 'Tab View 60s', 'Tab View 120s', 'Tab View 180s', 'Tab View 300s', 'Tab View 600s')
            ) as `urea`
        inner join
            `exp_users` as `eut`
        on
            `urea`.`unified_id` = `eut`.`app_tab_view_unified_id`
        where
            `eut`.`app_tab_view_unified_id` > 0
        and
            `urea`.`datetime` between `eut`.`exp_start_datetime` and `exp_end_datetime`
        group by
            `dt`,
            `variation`,
            `unified_id`
    ),
    `web_user_counts` as (
        select
            `eut`.`dt` as `dt`,
            `eut`.`variation` as `variation`,
            `eut`.`unified_id` as `unified_id`,
            ifNull(`wec`.`tab_view_events_cnt`, 0) as `tab_view_events_cnt`,
            ifNull(`wec`.`tab_view_60s_events_cnt`, 0) as `tab_view_60s_events_cnt`,
            ifNull(`wec`.`tab_view_120s_events_cnt`, 0) as `tab_view_120s_events_cnt`,
            ifNull(`wec`.`tab_view_180s_events_cnt`, 0) as `tab_view_180s_events_cnt`,
            ifNull(`wec`.`tab_view_300s_events_cnt`, 0) as `tab_view_300s_events_cnt`,
            ifNull(`wec`.`tab_view_600s_events_cnt`, 0) as `tab_view_600s_events_cnt`
        from
            `exp_users` as `eut`
        left join
            `web_event_counts` as `wec`
        on
            `eut`.`dt` = `wec`.`dt`
        and
            `eut`.`variation` = `wec`.`variation`
        and
            `eut`.`unified_id` = `wec`.`unified_id`
        where
            {calculate_web_tab_view_sql}
    ),
    `app_user_counts` as (
        select
            `eut`.`dt` as `dt`,
            `eut`.`variation` as `variation`,
            `eut`.`unified_id` as `unified_id`,
            ifNull(`aec`.`tab_view_events_cnt`, 0) as `tab_view_events_cnt`,
            ifNull(`aec`.`tab_view_60s_events_cnt`, 0) as `tab_view_60s_events_cnt`,
            ifNull(`aec`.`tab_view_120s_events_cnt`, 0) as `tab_view_120s_events_cnt`,
            ifNull(`aec`.`tab_view_180s_events_cnt`, 0) as `tab_view_180s_events_cnt`,
            ifNull(`aec`.`tab_view_300s_events_cnt`, 0) as `tab_view_300s_events_cnt`,
            ifNull(`aec`.`tab_view_600s_events_cnt`, 0) as `tab_view_600s_events_cnt`
        from
            `exp_users` as `eut`
        left join
            `app_event_counts` as `aec`
        on
            `eut`.`dt` = `aec`.`dt`
        and
            `eut`.`variation` = `aec`.`variation`
        and
            `eut`.`unified_id` = `aec`.`unified_id`
        where
            {calculate_app_tab_view_sql}
        and
            `eut`.`app_tab_view_unified_id` > 0
    ),
    `web_tab_view_metrics` as (
        select
            `dt`,
            `variation`,
            countIf(`wuc`.`tab_view_60s_events_cnt` > 0) as `tab_view_60s_user_cnt`,
            countIf(`wuc`.`tab_view_120s_events_cnt` > 0) as `tab_view_120s_user_cnt`,
            countIf(`wuc`.`tab_view_180s_events_cnt` > 0) as `tab_view_180s_user_cnt`,
            countIf(`wuc`.`tab_view_300s_events_cnt` > 0) as `tab_view_300s_user_cnt`,
            countIf(`wuc`.`tab_view_600s_events_cnt` > 0) as `tab_view_600s_user_cnt`,
            sum(`wuc`.`tab_view_events_cnt`) as `tab_view_events_cnt`,
            sum(`wuc`.`tab_view_60s_events_cnt`) as `tab_view_60s_events_cnt`,
            sum(`wuc`.`tab_view_120s_events_cnt`) as `tab_view_120s_events_cnt`,
            sum(`wuc`.`tab_view_180s_events_cnt`) as `tab_view_180s_events_cnt`,
            sum(`wuc`.`tab_view_300s_events_cnt`) as `tab_view_300s_events_cnt`,
            sum(`wuc`.`tab_view_600s_events_cnt`) as `tab_view_600s_events_cnt`,
            ifNotFinite(ifNull(varSamp(`wuc`.`tab_view_events_cnt`), 0), 0) as `tab_view_events_per_user_var`,
            ifNotFinite(ifNull(varSamp(`wuc`.`tab_view_60s_events_cnt`), 0), 0) as `tab_view_60s_events_per_user_var`,
            ifNotFinite(ifNull(varSamp(`wuc`.`tab_view_120s_events_cnt`), 0), 0) as `tab_view_120s_events_per_user_var`,
            ifNotFinite(ifNull(varSamp(`wuc`.`tab_view_180s_events_cnt`), 0), 0) as `tab_view_180s_events_per_user_var`,
            ifNotFinite(ifNull(varSamp(`wuc`.`tab_view_300s_events_cnt`), 0), 0) as `tab_view_300s_events_per_user_var`,
            ifNotFinite(ifNull(varSamp(`wuc`.`tab_view_600s_events_cnt`), 0), 0) as `tab_view_600s_events_per_user_var`
        from
            `web_user_counts` as `wuc`
        group by
            `dt`,
            `variation`
    ),
    `app_tab_view_metrics` as (
        select
            `dt`,
            `variation`,
            countIf(`auc`.`tab_view_60s_events_cnt` > 0) as `tab_view_60s_user_cnt`,
            countIf(`auc`.`tab_view_120s_events_cnt` > 0) as `tab_view_120s_user_cnt`,
            countIf(`auc`.`tab_view_180s_events_cnt` > 0) as `tab_view_180s_user_cnt`,
            countIf(`auc`.`tab_view_300s_events_cnt` > 0) as `tab_view_300s_user_cnt`,
            countIf(`auc`.`tab_view_600s_events_cnt` > 0) as `tab_view_600s_user_cnt`,
            sum(`auc`.`tab_view_events_cnt`) as `tab_view_events_cnt`,
            sum(`auc`.`tab_view_60s_events_cnt`) as `tab_view_60s_events_cnt`,
            sum(`auc`.`tab_view_120s_events_cnt`) as `tab_view_120s_events_cnt`,
            sum(`auc`.`tab_view_180s_events_cnt`) as `tab_view_180s_events_cnt`,
            sum(`auc`.`tab_view_300s_events_cnt`) as `tab_view_300s_events_cnt`,
            sum(`auc`.`tab_view_600s_events_cnt`) as `tab_view_600s_events_cnt`,
            ifNotFinite(ifNull(varSamp(`auc`.`tab_view_events_cnt`), 0), 0) as `tab_view_events_per_user_var`,
            ifNotFinite(ifNull(varSamp(`auc`.`tab_view_60s_events_cnt`), 0), 0) as `tab_view_60s_events_per_user_var`,
            ifNotFinite(ifNull(varSamp(`auc`.`tab_view_120s_events_cnt`), 0), 0) as `tab_view_120s_events_per_user_var`,
            ifNotFinite(ifNull(varSamp(`auc`.`tab_view_180s_events_cnt`), 0), 0) as `tab_view_180s_events_per_user_var`,
            ifNotFinite(ifNull(varSamp(`auc`.`tab_view_300s_events_cnt`), 0), 0) as `tab_view_300s_events_per_user_var`,
            ifNotFinite(ifNull(varSamp(`auc`.`tab_view_600s_events_cnt`), 0), 0) as `tab_view_600s_events_per_user_var`
        from
            `app_user_counts` as `auc`
        group by
            `dt`,
            `variation`
    )

select
    `days`.`dt` as `dt`,
    `days`.`variation` as `variation`,
    toUInt64(ifNull(`wtv`.`tab_view_60s_user_cnt`, 0)) as `web_tab_view_60s_user_cnt`,
    toUInt64(ifNull(`wtv`.`tab_view_120s_user_cnt`, 0)) as `web_tab_view_120s_user_cnt`,
    toUInt64(ifNull(`wtv`.`tab_view_180s_user_cnt`, 0)) as `web_tab_view_180s_user_cnt`,
    toUInt64(ifNull(`wtv`.`tab_view_300s_user_cnt`, 0)) as `web_tab_view_300s_user_cnt`,
    toUInt64(ifNull(`wtv`.`tab_view_600s_user_cnt`, 0)) as `web_tab_view_600s_user_cnt`,
    toUInt64(ifNull(`wtv`.`tab_view_events_cnt`, 0)) as `web_tab_view_events_cnt`,
    toUInt64(ifNull(`wtv`.`tab_view_60s_events_cnt`, 0)) as `web_tab_view_60s_events_cnt`,
    toUInt64(ifNull(`wtv`.`tab_view_120s_events_cnt`, 0)) as `web_tab_view_120s_events_cnt`,
    toUInt64(ifNull(`wtv`.`tab_view_180s_events_cnt`, 0)) as `web_tab_view_180s_events_cnt`,
    toUInt64(ifNull(`wtv`.`tab_view_300s_events_cnt`, 0)) as `web_tab_view_300s_events_cnt`,
    toUInt64(ifNull(`wtv`.`tab_view_600s_events_cnt`, 0)) as `web_tab_view_600s_events_cnt`,
    ifNull(`wtv`.`tab_view_events_per_user_var`, 0) as `web_tab_view_events_per_user_var`,
    ifNull(`wtv`.`tab_view_60s_events_per_user_var`, 0) as `web_tab_view_60s_events_per_user_var`,
    ifNull(`wtv`.`tab_view_120s_events_per_user_var`, 0) as `web_tab_view_120s_events_per_user_var`,
    ifNull(`wtv`.`tab_view_180s_events_per_user_var`, 0) as `web_tab_view_180s_events_per_user_var`,
    ifNull(`wtv`.`tab_view_300s_events_per_user_var`, 0) as `web_tab_view_300s_events_per_user_var`,
    ifNull(`wtv`.`tab_view_600s_events_per_user_var`, 0) as `web_tab_view_600s_events_per_user_var`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_60s_user_cnt`, 0))) as `app_tab_view_60s_user_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_120s_user_cnt`, 0))) as `app_tab_view_120s_user_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_180s_user_cnt`, 0))) as `app_tab_view_180s_user_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_300s_user_cnt`, 0))) as `app_tab_view_300s_user_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_600s_user_cnt`, 0))) as `app_tab_view_600s_user_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_events_cnt`, 0))) as `app_tab_view_events_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_60s_events_cnt`, 0))) as `app_tab_view_60s_events_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_120s_events_cnt`, 0))) as `app_tab_view_120s_events_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_180s_events_cnt`, 0))) as `app_tab_view_180s_events_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_300s_events_cnt`, 0))) as `app_tab_view_300s_events_cnt`,
    toUInt64(if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_600s_events_cnt`, 0))) as `app_tab_view_600s_events_cnt`,
    if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_events_per_user_var`, 0)) as `app_tab_view_events_per_user_var`,
    if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_60s_events_per_user_var`, 0)) as `app_tab_view_60s_events_per_user_var`,
    if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_120s_events_per_user_var`, 0)) as `app_tab_view_120s_events_per_user_var`,
    if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_180s_events_per_user_var`, 0)) as `app_tab_view_180s_events_per_user_var`,
    if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_300s_events_per_user_var`, 0)) as `app_tab_view_300s_events_per_user_var`,
    if({is_web_client_sql}, 0, ifNull(`atv`.`tab_view_600s_events_per_user_var`, 0)) as `app_tab_view_600s_events_per_user_var`,
    toUInt64(if({is_web_client_sql}, ifNull(`atv`.`tab_view_60s_user_cnt`, 0), 0)) as `mobweb_app_tab_view_60s_user_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`atv`.`tab_view_120s_user_cnt`, 0), 0)) as `mobweb_app_tab_view_120s_user_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`atv`.`tab_view_180s_user_cnt`, 0), 0)) as `mobweb_app_tab_view_180s_user_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`atv`.`tab_view_300s_user_cnt`, 0), 0)) as `mobweb_app_tab_view_300s_user_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`atv`.`tab_view_600s_user_cnt`, 0), 0)) as `mobweb_app_tab_view_600s_user_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`atv`.`tab_view_events_cnt`, 0), 0)) as `mobweb_app_tab_view_events_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`atv`.`tab_view_60s_events_cnt`, 0), 0)) as `mobweb_app_tab_view_60s_events_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`atv`.`tab_view_120s_events_cnt`, 0), 0)) as `mobweb_app_tab_view_120s_events_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`atv`.`tab_view_180s_events_cnt`, 0), 0)) as `mobweb_app_tab_view_180s_events_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`atv`.`tab_view_300s_events_cnt`, 0), 0)) as `mobweb_app_tab_view_300s_events_cnt`,
    toUInt64(if({is_web_client_sql}, ifNull(`atv`.`tab_view_600s_events_cnt`, 0), 0)) as `mobweb_app_tab_view_600s_events_cnt`,
    if({is_web_client_sql}, ifNull(`atv`.`tab_view_events_per_user_var`, 0), 0) as `mobweb_app_tab_view_events_per_user_var`,
    if({is_web_client_sql}, ifNull(`atv`.`tab_view_60s_events_per_user_var`, 0), 0) as `mobweb_app_tab_view_60s_events_per_user_var`,
    if({is_web_client_sql}, ifNull(`atv`.`tab_view_120s_events_per_user_var`, 0), 0) as `mobweb_app_tab_view_120s_events_per_user_var`,
    if({is_web_client_sql}, ifNull(`atv`.`tab_view_180s_events_per_user_var`, 0), 0) as `mobweb_app_tab_view_180s_events_per_user_var`,
    if({is_web_client_sql}, ifNull(`atv`.`tab_view_300s_events_per_user_var`, 0), 0) as `mobweb_app_tab_view_300s_events_per_user_var`,
    if({is_web_client_sql}, ifNull(`atv`.`tab_view_600s_events_per_user_var`, 0), 0) as `mobweb_app_tab_view_600s_events_per_user_var`
from
    (
        select distinct
            `dt`,
            `variation`
        from
            `exp_users`
    ) as `days`
left join
    `web_tab_view_metrics` as `wtv`
on
    `days`.`dt` = `wtv`.`dt`
and
    `days`.`variation` = `wtv`.`variation`
left join
    `app_tab_view_metrics` as `atv`
on
    `days`.`dt` = `atv`.`dt`
and
    `days`.`variation` = `atv`.`variation`
order by
    `dt`,
    `variation`
