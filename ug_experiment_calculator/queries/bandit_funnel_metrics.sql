with
    toDateTime({exp_start_ts}) as `exp_window_start_dt`,
    if({exp_end_ts} <= {exp_start_ts}, now(), toDateTime({exp_end_ts})) as `exp_window_end_dt`,
    `exp_users` as (
        select distinct
            `unified_id`,
            `variation`,
            `arm`,
            toDate(toDateTime(`exp_start_dt`, 'UTC')) as `dt`
        from {exp_users_table}
        where
            `client` = {client_sql}
        and
            `segment` = {segment_sql}
        and
            `segment_hash` = {segment_hash_sql}
    ),
    `funnel_events` as (
        select
            `urew`.`unified_id` as `unified_id`,
            `urew`.`event` as `event_name`,
            `urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'aix_variant_id')] as `event_arm_param`
        from
            `default`.`ug_rt_events_web` as `urew`
        where
            `urew`.`date` between toDate(`exp_window_start_dt`) and toDate(`exp_window_end_dt`)
        and
            `urew`.`datetime` between `exp_window_start_dt` and `exp_window_end_dt`
        and
            `urew`.`unified_id` > 0
        and
            `urew`.`is_bot` = 0
        and
            `urew`.`event` in ('Permanent Banner View', 'Permanent Banner Click', 'Permanent Banner Close', 'Landing Plans View', 'Landing Checkout View', 'PURCHASE_SUCCESS')
        and
            `urew`.`params.str_value`[indexOf(`urew`.`params.key`, 'aix_experiment_id')] = {aix_experiment_id_sql}
    ),
    `user_steps` as (
        select
            `eut`.`dt` as `dt`,
            if(`fev`.`event_arm_param` != '', `fev`.`event_arm_param`, `eut`.`arm`) as `event_arm`,
            {arm_variation_sql} as `event_variation`,
            `fev`.`event_name` as `event_name`,
            `eut`.`unified_id` as `step_unified_id`
        from
            `funnel_events` as `fev`
        inner join
            `exp_users` as `eut`
        on
            `fev`.`unified_id` = `eut`.`unified_id`
    ),
    `step_counts` as (
        select
            `ust`.`dt` as `dt`,
            `ust`.`event_variation` as `variation`,
            uniqExactIf(`ust`.`step_unified_id`, `ust`.`event_name` = 'Permanent Banner View') as `banner_view_user_cnt_raw`,
            uniqExactIf(`ust`.`step_unified_id`, `ust`.`event_name` = 'Permanent Banner Click') as `banner_click_user_cnt_raw`,
            uniqExactIf(`ust`.`step_unified_id`, `ust`.`event_name` = 'Permanent Banner Close') as `banner_close_user_cnt_raw`,
            uniqExactIf(`ust`.`step_unified_id`, `ust`.`event_name` = 'Landing Plans View') as `plans_view_user_cnt_raw`,
            uniqExactIf(`ust`.`step_unified_id`, `ust`.`event_name` = 'Landing Checkout View') as `checkout_view_user_cnt_raw`,
            uniqExactIf(`ust`.`step_unified_id`, `ust`.`event_name` = 'PURCHASE_SUCCESS') as `purchase_success_user_cnt_raw`
        from
            `user_steps` as `ust`
        where
            `ust`.`event_variation` > 0
        group by
            `dt`,
            `variation`
    ),
    `participant_counts` as (
        select
            `eut`.`dt` as `dt`,
            `eut`.`variation` as `variation`,
            uniqExact(`eut`.`unified_id`) as `participants_raw`
        from
            `exp_users` as `eut`
        group by
            `dt`,
            `variation`
    ),
    `combined` as (
        select
            `pac`.`dt` as `dt`,
            `pac`.`variation` as `variation`,
            `pac`.`participants_raw` as `participants_raw`,
            toUInt64(0) as `banner_view_user_cnt_raw`,
            toUInt64(0) as `banner_click_user_cnt_raw`,
            toUInt64(0) as `banner_close_user_cnt_raw`,
            toUInt64(0) as `plans_view_user_cnt_raw`,
            toUInt64(0) as `checkout_view_user_cnt_raw`,
            toUInt64(0) as `purchase_success_user_cnt_raw`
        from
            `participant_counts` as `pac`
        union all
        select
            `stc`.`dt` as `dt`,
            `stc`.`variation` as `variation`,
            toUInt64(0) as `participants_raw`,
            `stc`.`banner_view_user_cnt_raw` as `banner_view_user_cnt_raw`,
            `stc`.`banner_click_user_cnt_raw` as `banner_click_user_cnt_raw`,
            `stc`.`banner_close_user_cnt_raw` as `banner_close_user_cnt_raw`,
            `stc`.`plans_view_user_cnt_raw` as `plans_view_user_cnt_raw`,
            `stc`.`checkout_view_user_cnt_raw` as `checkout_view_user_cnt_raw`,
            `stc`.`purchase_success_user_cnt_raw` as `purchase_success_user_cnt_raw`
        from
            `step_counts` as `stc`
    )

select
    `cmb`.`dt` as `dt`,
    `cmb`.`variation` as `variation`,
    sum(`cmb`.`participants_raw`) as `participants`,
    sum(`cmb`.`banner_view_user_cnt_raw`) as `banner_view_user_cnt`,
    sum(`cmb`.`banner_click_user_cnt_raw`) as `banner_click_user_cnt`,
    sum(`cmb`.`banner_close_user_cnt_raw`) as `banner_close_user_cnt`,
    sum(`cmb`.`plans_view_user_cnt_raw`) as `plans_view_user_cnt`,
    sum(`cmb`.`checkout_view_user_cnt_raw`) as `checkout_view_user_cnt`,
    sum(`cmb`.`purchase_success_user_cnt_raw`) as `purchase_success_user_cnt`
from
    `combined` as `cmb`
group by
    `dt`,
    `variation`
order by
    `dt`,
    `variation`
