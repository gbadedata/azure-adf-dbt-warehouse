-- No email, name or hash: the mart identifies supporters by CRM ID only.
select
    s.supporter_id,
    s.acquisition_campaign_id,
    s.postcode_district,
    s.has_marketing_consent,
    s.supporter_status,
    cast(s.created_at as date)                       as created_date,
    cast(g.first_paid_donation_at as date)           as first_paid_donation_date,
    coalesce(g.paid_donations, 0)                    as paid_donations,
    coalesce(g.lifetime_net_gbp, 0)                  as lifetime_net_gbp,
    cast(case when g.paid_donations > 0 then 1 else 0 end as bit) as is_donor
from {{ ref('stg_crm__supporters') }} as s
left join {{ ref('int_supporters__giving') }} as g
    on g.supporter_id = s.supporter_id
