select
    campaign_id,
    campaign_name,
    channel,
    start_date,
    end_date,
    budget_gbp
from {{ ref('stg_crm__campaigns') }}
