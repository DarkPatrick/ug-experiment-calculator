with
    {exp_id} as `exp_id_value`,
    toDate('{date_filter}') as `date_filter`,
    toDateTime({date_start_ts}) as `date_start_dt`,
    toDateTime({date_end_ts}) as `date_end_dt`

select
    toUInt32(`exp_id_value`) as `exp_id`,
    {client_sql} as `client`,
    toUInt64(`{alias}`.`unified_id`) as `unified_id`,
    toUInt16(`{alias}`.`experiments.variation`[indexOf(`{alias}`.`experiments.id`, `exp_id_value`)]) as `variation`,
    min(`{alias}`.`datetime`) as `first_split_dt`
from
    {events_table} as `{alias}`
left join (
    select
        `client`,
        `unified_id`
    from
        {split_users_table}
    where
        `client` = {client_sql}
) as `saved`
on
    `saved`.`client` = {client_sql}
and
    `saved`.`unified_id` = toUInt64(`{alias}`.`unified_id`)
where
        `{alias}`.`date` = `date_filter`
    and
        `{alias}`.`datetime` between `date_start_dt` and `date_end_dt`
    and
        `{alias}`.`unified_id` > 0
    and
        ifNull(`saved`.`unified_id`, 0) = 0
    and
        `{alias}`.`source` = {client_sql}
    and
        has(`{alias}`.`experiments.id`, `exp_id_value`)
    and
        `{alias}`.`experiments.variation`[indexOf(`{alias}`.`experiments.id`, `exp_id_value`)] > 0
    {platform_filter}
group by
    `unified_id`,
    `variation`
order by
    `first_split_dt`
