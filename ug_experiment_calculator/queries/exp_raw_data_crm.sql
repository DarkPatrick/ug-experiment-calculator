with
    toDateTime({exp_start_ts}) as `exp_window_start_dt`,
    if({exp_end_ts} <= {exp_start_ts}, now(), toDateTime({exp_end_ts})) as `exp_window_end_dt`


-- CRM (Bloomreach) cohort: one row per Bloomreach recipient (`user_id`) of the
-- campaign's 'AB Test Start'. The participant key is the synthetic `unified_id`
-- = -user_id, so Members equal the CRM dashboard (uniqExact(user_id)), and real
-- identities go to `subscription_unified_ids`: the event's unified_id plus every
-- unified_id the same user_id subscribed under in the attribution window. Only
-- ~1/3 of the purchases of a recipient are made under the event's unified_id
-- (exp 8018), so a unified_id-only join would lose most of them.
select
    -toInt64(`par`.`user_id`) as `unified_id`,
    `par`.`variation` as `variation`,
    `par`.`exp_start_dt` as `exp_start_dt`,
    `par`.`rights` as `rights`,
    `par`.`user_id` as `user_id`,
    `par`.`payment_account_id` as `payment_account_id`,
    `par`.`country` as `country`,
    toUInt8(1) as `auth`,
    toInt64(0) as `app_unified_id`,
    toUInt8(0) as `has_app`,
    arrayDistinct(arrayFilter(x -> x > 0, arrayConcat(`par`.`event_unified_ids`, `sub`.`sub_unified_ids`))) as `subscription_unified_ids`,
    '( Other )' as `os`,
    '( Other )' as `browser`,
    cast([], 'Array(UInt32)') as `frontend_release_version`,
    cast([], 'Array(UInt32)') as `backend_release_version`,
    cast([], 'Array(UInt32)') as `web_version`,
    toInt64(0) as `platform`,
    '' as `type`,
    toUInt8(0) as `is_new`,
    '' as `connection`,
    '( Other )' as `device_manufacturer`
from (
    select
        `bce`.`user_id` as `user_id`,
        min(greatest(
            `bce`.`experiments.variation`[indexOf(`bce`.`experiments.id`, {exp_id_sql})],
            `bce`.`experiments.variation`[indexOf(`bce`.`experiments.id`, {provider_experiment_id_sql})]
        )) as `variation`,
        min(toUnixTimestamp(`bce`.`datetime`)) as `exp_start_dt`,
        toUInt64(0) as `rights`,
        argMinIf(`bce`.`payment_account_id`, `bce`.`datetime`, `bce`.`payment_account_id` > 0) as `payment_account_id`,
        argMin(`bce`.`bm_country`, `bce`.`datetime`) as `country`,
        groupUniqArrayIf(toInt64(`bce`.`unified_id`), `bce`.`unified_id` > 0) as `event_unified_ids`
    from (
        -- The segment's event filter (uwf) runs on raw events, as in the other cohort queries.
        select
            *
        from
            `monetisation_crm`.`ug_bloomreach_campaign_events`
        where
            ({where_sql})
    ) as `bce`
    where
        `bce`.`date` between toDate(`exp_window_start_dt`) and toDate(`exp_window_end_dt`)
    and
        `bce`.`datetime` between `exp_window_start_dt` and `exp_window_end_dt`
    and
        `bce`.`bm_campaign_id` = {provider_experiment_id_sql}
    and
        `bce`.`user_id` > 0
    and
        greatest(
            `bce`.`experiments.variation`[indexOf(`bce`.`experiments.id`, {exp_id_sql})],
            `bce`.`experiments.variation`[indexOf(`bce`.`experiments.id`, {provider_experiment_id_sql})]
        ) > 0
    group by
        `user_id`
    having
        ({having_sql})
) as `par`
left join (
    select
        `sbs`.`user_id` as `user_id`,
        groupUniqArray(toInt64(`sbs`.`unified_id`)) as `sub_unified_ids`
    from
        {subscriptions_table} as `sbs`
    where
        `sbs`.`user_id` > 0
    and
        `sbs`.`unified_id` > 0
    and
        `sbs`.`subscribed_dt` between toUnixTimestamp(`exp_window_start_dt`) and toUnixTimestamp(`exp_window_end_dt`) + {attribution_window_seconds}
    group by
        `user_id`
) as `sub`
on
    `par`.`user_id` = `sub`.`user_id`
