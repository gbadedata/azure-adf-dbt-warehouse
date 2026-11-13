-- What each campaign brought in, against what it cost. One row per campaign.
with acquired as (
    select acquisition_campaign_id as campaign_id, count(*) as supporters_acquired
    from {{ ref('dim_supporters') }}
    group by acquisition_campaign_id
),

giving as (
    select
        campaign_id,
        count(distinct case when donation_status = 'paid' then supporter_id end)          as donors,
        sum(case when donation_status = 'paid' then 1 else 0 end)                         as paid_donations,
        sum(case when donation_status in ('paid', 'refunded') then amount_gbp else 0 end) as gross_gbp,
        sum(case when donation_status = 'refunded' then amount_gbp else 0 end)            as refunded_gbp,
        sum(net_amount_gbp)                                                               as net_gbp,
        sum(gift_aid_gbp)                                                                 as gift_aid_gbp,
        sum(case when donation_status = 'pending' then amount_gbp else 0 end)             as pending_gbp
    from {{ ref('fct_donations') }}
    group by campaign_id
),

events as (
    select
        campaign_id,
        count(*)                     as event_registrations,
        sum(fundraising_target_gbp)  as event_fundraising_target_gbp
    from {{ ref('fct_event_registrations') }}
    group by campaign_id
)

select
    c.campaign_id,
    c.campaign_name,
    c.channel,
    c.start_date,
    c.end_date,
    c.budget_gbp,
    coalesce(a.supporters_acquired, 0)                    as supporters_acquired,
    coalesce(g.donors, 0)                                 as donors,
    coalesce(g.paid_donations, 0)                         as paid_donations,
    cast(coalesce(g.gross_gbp, 0) as decimal(14, 2))      as gross_gbp,
    cast(coalesce(g.refunded_gbp, 0) as decimal(14, 2))   as refunded_gbp,
    cast(coalesce(g.net_gbp, 0) as decimal(14, 2))        as net_gbp,
    cast(coalesce(g.gift_aid_gbp, 0) as decimal(14, 2))   as gift_aid_gbp,
    cast(coalesce(g.pending_gbp, 0) as decimal(14, 2))    as pending_gbp,
    coalesce(e.event_registrations, 0)                    as event_registrations,
    cast(coalesce(e.event_fundraising_target_gbp, 0) as decimal(14, 2)) as event_fundraising_target_gbp,
    cast(c.budget_gbp / nullif(a.supporters_acquired, 0) as decimal(14, 2))  as cost_per_supporter_gbp,
    cast((coalesce(g.net_gbp, 0) + coalesce(g.gift_aid_gbp, 0)) / nullif(c.budget_gbp, 0) as decimal(10, 2))
                                                          as income_per_pound_spent
from {{ ref('dim_campaigns') }} as c
left join acquired as a on a.campaign_id = c.campaign_id
left join giving as g on g.campaign_id = c.campaign_id
left join events as e on e.campaign_id = c.campaign_id
