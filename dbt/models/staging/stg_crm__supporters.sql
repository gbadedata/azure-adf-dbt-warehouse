with source as (
    select * from {{ source('crm', 'crm_supporters') }}
),

parsed as (
    select
        record_id                                                             as supporter_id,
        lower(trim(json_value(payload, '$.email')))                           as email,
        upper(json_value(payload, '$.postcode_district'))                     as postcode_district,
        json_value(payload, '$.acquisition_campaign_id')                      as acquisition_campaign_id,
        cast(case json_value(payload, '$.marketing_consent')
                 when 'true' then 1 else 0 end as bit)                        as has_marketing_consent,
        lower(json_value(payload, '$.status'))                                as supporter_status,
        try_convert(datetimeoffset(3), json_value(payload, '$.created_at'), 127) as created_at,
        source_updated_at,
        _last_loaded_at
    from source
)

select
    *,
    -- A pseudonymous key for matching other sources by email without carrying the address forward.
    {{ email_hash('email') }} as email_hash
from parsed
