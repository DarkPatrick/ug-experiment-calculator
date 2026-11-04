-- Cohort window of a CRM (Bloomreach) experiment: its 'AB Test Start' events,
-- plus the UTM campaigns its recipients clicked (the "after click" attribution).
select
    minIf(`bce`.`datetime`, `bce`.`event` = 'AB Test Start') as `first_event_dt`,
    maxIf(`bce`.`datetime`, `bce`.`event` = 'AB Test Start') as `last_event_dt`,
    -- the mass send: a stray early start (a test send, a single re-queued recipient) must not move the split date
    quantileExactIf(0.01)(`bce`.`datetime`, `bce`.`event` = 'AB Test Start') as `send_dt`,
    uniqExactIf(`bce`.`user_id`, `bce`.`event` = 'AB Test Start') as `users_cnt`,
    arraySort(groupUniqArrayIf(
        lower(`bce`.`bm_utm_campaign`),
        `bce`.`event` = 'Email Clicked'
        and `bce`.`bm_utm_campaign` != ''
        and countMatches(`bce`.`bm_campaign_url`, 'consent') = 0
    )) as `clicked_utm_campaigns`
from
    `monetisation_crm`.`ug_bloomreach_campaign_events` as `bce`
where
    `bce`.`date` between toDate({date_start_ts}) - interval 1 day and toDate({date_scan_end_ts}) + interval {attribution_window_days} day
and
    `bce`.`bm_campaign_id` = {provider_experiment_id_sql}
and
    `bce`.`event` in ('AB Test Start', 'Email Clicked')
