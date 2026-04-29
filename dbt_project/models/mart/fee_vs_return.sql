with latest as (
    select *
    from {{ ref('stg_apra_mysuper') }}
    where quarter_date = (select max(quarter_date) from {{ ref('stg_apra_mysuper') }})
      and total_fee_pct is not null
      and return_1yr    is not null
),

medians as (
    select
        percentile_cont(0.5) within group (order by total_fee_pct) as median_fee,
        percentile_cont(0.5) within group (order by return_1yr)    as median_return
    from latest
),

classified as (
    select
        l.fund_name,
        l.abn,
        l.fund_type,
        l.quarter_date,
        l.total_fee_pct,
        l.return_1yr,
        l.return_5yr,
        l.net_assets_m,
        m.median_fee,
        m.median_return,
        case
            when l.total_fee_pct <= m.median_fee   and l.return_1yr >= m.median_return then 'best_value'
            when l.total_fee_pct >  m.median_fee   and l.return_1yr >= m.median_return then 'expensive_performer'
            when l.total_fee_pct <= m.median_fee   and l.return_1yr <  m.median_return then 'cheap_underperformer'
            else 'worst_value'
        end as value_quadrant
    from latest l
    cross join medians m
)

select * from classified
