with base as (
    select * from {{ ref('stg_apra_mysuper') }}
    where return_5yr is not null
),

ranked as (
    select
        fund_name,
        abn,
        fund_type,
        quarter_date,
        return_1yr,
        return_3yr,
        return_5yr,
        total_fee_pct,
        net_assets_m,
        member_accounts,

        -- rank within peer group each quarter
        rank() over (partition by quarter_date, fund_type order by return_5yr desc)  as rank_5yr_in_type,
        rank() over (partition by quarter_date              order by return_5yr desc)  as rank_5yr_overall,

        -- fee percentile (lower = cheaper)
        percent_rank() over (partition by quarter_date order by total_fee_pct)        as fee_percentile,

        count(*) over (partition by quarter_date)                                     as total_funds_that_quarter

    from base
)

select * from ranked
