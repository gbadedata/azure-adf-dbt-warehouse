with source as (
    select * from {{ source('events', 'event_registrations') }}
)

select
    registration_id,
    event_name,
    try_convert(date, event_date, 23)                                  as event_date,
    campaign_id,
    lower(trim(supporter_email))                                       as email,
    {{ email_hash('supporter_email') }}                                as email_hash,
    try_convert(datetimeoffset(3), registered_at, 127)                 as registered_at,
    lower(ticket_type)                                                 as ticket_type,
    try_cast(fundraising_target_gbp as decimal(12, 2))                 as fundraising_target_gbp,
    _source_file,
    _last_loaded_at
from source
