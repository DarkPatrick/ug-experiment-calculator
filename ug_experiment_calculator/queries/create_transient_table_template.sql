create table {full_table_name} on cluster {cluster}
{schema}
engine = MergeTree
PARTITION BY ({partition})
ORDER BY ({sorting})
SETTINGS index_granularity = 8192
