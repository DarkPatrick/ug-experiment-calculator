-- Email engagement of a CRM (Bloomreach) cohort, one 0/1 flag per participant:
-- did the recipient get a Sent / Delivered / Open / Click / Consent Reject event
-- of this campaign within the attribution window after their AB Test Start.
-- Unique users, not (campaign, action, date) tuples as on the CRM Redash
-- dashboard, so every rate is a proportion of participants and gets a z-test.
with
    `exp_users` as (
        select
            `eut`.`unified_id` as `unified_id`,
            `eut`.`user_id` as `user_id`,
            `eut`.`variation` as `variation`,
            `eut`.`exp_start_dt` as `exp_start_dt`
        from
            {exp_users_table} as `eut`
        where
            `eut`.`client` = {client_sql}
        and
            `eut`.`segment` = {segment_sql}
        and
            `eut`.`segment_hash` = {segment_hash_sql}
    ),
    `email_events` as (
        select
            `bce`.`user_id` as `user_id`,
            `bce`.`datetime` as `datetime`,
            `bce`.`event` as `event`
        from
            `monetisation_crm`.`ug_bloomreach_campaign_events` as `bce`
        where
            `bce`.`date` between toDate('{date_start}') and toDate('{date_end}') + interval {attribution_window_days} day
        and
            `bce`.`bm_campaign_id` = {provider_experiment_id_sql}
        and
            `bce`.`event` in ('Email Sent', 'Email Delivered', 'Email Open', 'Email Clicked', 'Consent Reject')
        and (
            `bce`.`event` != 'Email Clicked'
            or countMatches(`bce`.`bm_campaign_url`, 'consent') = 0
        )
    ),
    `user_flags` as (
        select
            `eus`.`unified_id` as `unified_id`,
            `eus`.`variation` as `variation`,
            toDate(toDateTime(`eus`.`exp_start_dt`)) as `dt`,
            -- an unmatched left-join row has an empty event, so every countIf below is 0 for it
            toUInt8(countIf(`eme`.`event` = 'Email Sent' and `eme`.`datetime` between toDateTime(`eus`.`exp_start_dt`) and toDateTime(`eus`.`exp_start_dt`) + {attribution_window_seconds}) > 0) as `is_sent`,
            toUInt8(countIf(`eme`.`event` = 'Email Delivered' and `eme`.`datetime` between toDateTime(`eus`.`exp_start_dt`) and toDateTime(`eus`.`exp_start_dt`) + {attribution_window_seconds}) > 0) as `is_delivered`,
            toUInt8(countIf(`eme`.`event` = 'Email Open' and `eme`.`datetime` between toDateTime(`eus`.`exp_start_dt`) and toDateTime(`eus`.`exp_start_dt`) + {attribution_window_seconds}) > 0) as `is_opened`,
            toUInt8(countIf(`eme`.`event` = 'Email Clicked' and `eme`.`datetime` between toDateTime(`eus`.`exp_start_dt`) and toDateTime(`eus`.`exp_start_dt`) + {attribution_window_seconds}) > 0) as `is_clicked`,
            toUInt8(countIf(`eme`.`event` = 'Consent Reject' and `eme`.`datetime` between toDateTime(`eus`.`exp_start_dt`) and toDateTime(`eus`.`exp_start_dt`) + {attribution_window_seconds}) > 0) as `is_unsubscribed`
        from
            `exp_users` as `eus`
        left join
            `email_events` as `eme`
        on
            `eus`.`user_id` = `eme`.`user_id`
        group by
            `unified_id`,
            `variation`,
            `dt`
    )
select
    `ufl`.`dt` as `dt`,
    `ufl`.`variation` as `variation`,
    count() as `crm_participants`,
    sum(`ufl`.`is_sent`) as `email_sent_user_cnt`,
    sum(`ufl`.`is_delivered`) as `email_delivered_user_cnt`,
    sum(`ufl`.`is_opened`) as `email_opened_user_cnt`,
    sum(`ufl`.`is_clicked`) as `email_clicked_user_cnt`,
    sum(`ufl`.`is_unsubscribed`) as `email_unsubscribed_user_cnt`,
    sum(`ufl`.`is_opened` * `ufl`.`is_delivered`) as `email_delivered_opened_user_cnt`,
    sum(`ufl`.`is_clicked` * `ufl`.`is_opened`) as `email_opened_clicked_user_cnt`,
    sum(`ufl`.`is_clicked` * `ufl`.`is_delivered`) as `email_delivered_clicked_user_cnt`
from
    `user_flags` as `ufl`
group by
    `dt`,
    `variation`
order by
    `dt`,
    `variation`
