-- Fails if the campaign report does not add up to the donations it summarises.
with report as (
    select sum(net_gbp) as net_gbp, sum(refunded_gbp) as refunded_gbp, sum(paid_donations) as paid_donations
    from {{ ref('mart_campaign_performance') }}
),

facts as (
    select
        sum(net_amount_gbp)                                                    as net_gbp,
        sum(case when donation_status = 'refunded' then amount_gbp else 0 end) as refunded_gbp,
        sum(case when donation_status = 'paid' then 1 else 0 end)              as paid_donations
    from {{ ref('fct_donations') }}
)

select r.*, f.net_gbp as fact_net_gbp, f.refunded_gbp as fact_refunded_gbp, f.paid_donations as fact_paid
from report as r
cross join facts as f
where coalesce(r.net_gbp, 0) <> coalesce(f.net_gbp, 0)
   or coalesce(r.refunded_gbp, 0) <> coalesce(f.refunded_gbp, 0)
   or coalesce(r.paid_donations, 0) <> coalesce(f.paid_donations, 0)
