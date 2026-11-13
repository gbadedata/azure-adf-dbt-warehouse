-- Giving history per supporter, from donations in their latest state.
select
    supporter_id,
    min(case when donation_status = 'paid' then received_at end)                    as first_paid_donation_at,
    sum(case when donation_status = 'paid' then 1 else 0 end)                       as paid_donations,
    sum(case when donation_status = 'paid' then amount_gbp else 0 end)              as lifetime_net_gbp,
    sum(case when donation_status = 'refunded' then 1 else 0 end)                   as refunded_donations
from {{ ref('stg_crm__donations') }}
group by supporter_id
