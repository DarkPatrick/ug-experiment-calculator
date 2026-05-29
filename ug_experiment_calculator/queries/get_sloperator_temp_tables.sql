select
    distinct `tab`.`table_name` as `table_name`
from
    `information_schema`.`tables` as `tab`
where
    `tab`.`table_schema` = '{database}'
and (
    `tab`.`table_name` like '{table_prefix}exp_users_%'
or `tab`.`table_name` like '{table_prefix}exp_subscription_%'
)
