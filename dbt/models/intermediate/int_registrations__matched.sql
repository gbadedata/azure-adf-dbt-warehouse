-- Event registrations matched to CRM supporters on the hashed email. Registrations
-- from people not in the CRM keep a null supporter_id rather than being dropped.
with supporters as (
    select
        email_hash,
        min(supporter_id) as supporter_id   -- one supporter per address, deterministically
    from {{ ref('stg_crm__supporters') }}
    where email_hash is not null
    group by email_hash
)

select
    r.registration_id,
    r.event_name,
    r.event_date,
    r.campaign_id,
    s.supporter_id,
    r.registered_at,
    r.ticket_type,
    r.fundraising_target_gbp
from {{ ref('stg_events__registrations') }} as r
left join supporters as s
    on s.email_hash = r.email_hash
