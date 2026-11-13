-- Fails if a donation or a registration is lost (or invented) between raw and marts.
with checks as (
    select 'donations' as entity,
           (select count(*) from {{ source('crm', 'crm_donations') }}) as raw_rows,
           (select count(*) from {{ ref('fct_donations') }})           as mart_rows
    union all
    select 'supporters',
           (select count(*) from {{ source('crm', 'crm_supporters') }}),
           (select count(*) from {{ ref('dim_supporters') }})
    union all
    select 'event_registrations',
           (select count(*) from {{ source('events', 'event_registrations') }}),
           (select count(*) from {{ ref('fct_event_registrations') }})
)

select * from checks where raw_rows <> mart_rows
