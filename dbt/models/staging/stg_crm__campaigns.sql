with source as (
    select * from {{ source('crm', 'crm_campaigns') }}
)

select
    record_id                                                        as campaign_id,
    json_value(payload, '$.name')                                    as campaign_name,
    lower(json_value(payload, '$.channel'))                          as channel,
    try_convert(date, json_value(payload, '$.start_date'), 23)       as start_date,
    try_convert(date, json_value(payload, '$.end_date'), 23)         as end_date,
    try_cast(json_value(payload, '$.budget_gbp') as decimal(12, 2))  as budget_gbp,
    source_updated_at,
    _last_loaded_at
from source
