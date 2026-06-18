select
    toInt64(0) as `unified_id`,
    toUInt32(0) as `variation`,
    toUInt32(0) as `exp_start_dt`,
    toInt64(0) as `rights`,
    toInt64(0) as `user_id`,
    toUInt64(0) as `payment_account_id`,
    toString('') as `country`,
    toUInt8(0) as `auth`,
    toInt64(0) as `app_unified_id`,
    toUInt8(0) as `has_app`,
    cast([], 'Array(Int64)') as `subscription_unified_ids`
