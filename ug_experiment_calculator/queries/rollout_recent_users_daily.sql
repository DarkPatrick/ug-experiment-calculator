with
    toDate('{date_start}') as `date_start`,
    toDate('{date_end}') as `date_end`

select
    {client_sql} as `client`,
    uniqExact(`{alias}`.`unified_id`) / (dateDiff('day', `date_start`, `date_end`) + 1) as `users_avg`
from
    {events_table} as `{alias}`
where
    `{alias}`.`date` between `date_start` and `date_end`
and
    `{alias}`.`unified_id` > 0
and
    `{alias}`.`source` = {client_sql}
{platform_filter}
{country_filter}
