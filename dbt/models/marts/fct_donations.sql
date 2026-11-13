-- One row per donation in its latest state. Net income counts paid gifts only:
-- a refund sets it to zero and a pending gift is not income yet.
select
    donation_id,
    supporter_id,
    campaign_id,
    cast(received_at as date)                                              as received_date,
    received_at,
    payment_method,
    donation_status,
    amount_gbp,
    cast(case when donation_status = 'refunded' then 1 else 0 end as bit)  as is_refunded,
    cast(case when donation_status = 'paid' then amount_gbp else 0 end as decimal(12, 2)) as net_amount_gbp,
    is_gift_aid_declared,
    cast(
        case when donation_status = 'paid' and is_gift_aid_declared = 1
             then round(amount_gbp * {{ var('gift_aid_rate') }}, 2) else 0 end
        as decimal(12, 2)
    )                                                                      as gift_aid_gbp
from {{ ref('stg_crm__donations') }}
