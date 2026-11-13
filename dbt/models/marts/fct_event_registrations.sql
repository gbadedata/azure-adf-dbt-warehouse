select
    registration_id,
    event_name,
    event_date,
    campaign_id,
    supporter_id,
    cast(case when supporter_id is not null then 1 else 0 end as bit) as is_known_supporter,
    cast(registered_at as date)                                      as registered_date,
    ticket_type,
    fundraising_target_gbp
from {{ ref('int_registrations__matched') }}
