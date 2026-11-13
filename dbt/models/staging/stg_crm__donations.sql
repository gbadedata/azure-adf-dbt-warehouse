with source as (
    select * from {{ source('crm', 'crm_donations') }}
)

select
    record_id                                                              as donation_id,
    json_value(payload, '$.supporter_id')                                  as supporter_id,
    json_value(payload, '$.campaign_id')                                   as campaign_id,
    try_cast(json_value(payload, '$.amount') as decimal(12, 2))            as amount_gbp,
    upper(json_value(payload, '$.currency'))                               as currency,
    lower(json_value(payload, '$.payment_method'))                         as payment_method,
    lower(json_value(payload, '$.status'))                                 as donation_status,
    cast(case json_value(payload, '$.gift_aid') when 'true' then 1 else 0 end as bit) as is_gift_aid_declared,
    try_convert(datetimeoffset(3), json_value(payload, '$.received_at'), 127) as received_at,
    source_updated_at,
    _last_loaded_at
from source
