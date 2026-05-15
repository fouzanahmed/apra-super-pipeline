with source as (
    select * from {{ source('raw', 'apra_mysuper') }}
),

cleaned as (
    select
        -- identifiers
        trim(cast(fund_name    as text))                   as fund_name,
        trim(cast(abn         as text))                   as abn,
        trim(cast(product_name as text))                  as product_name,

        -- time dimension
        cast(quarter_year as date)                         as quarter_date,

        -- returns (stored as decimals, e.g. 0.0852 = 8.52%)
        cast(return_1yr  as numeric(10, 6))                as return_1yr,
        cast(return_3yr  as numeric(10, 6))                as return_3yr,
        cast(return_5yr  as numeric(10, 6))                as return_5yr,

        -- fees
        cast(investment_fee_pct as numeric(10, 6))         as investment_fee_pct,
        cast(admin_fee_pct      as numeric(10, 6))         as admin_fee_pct,
        cast(total_fee_pct      as numeric(10, 6))         as total_fee_pct,

        -- assets / members
        cast(net_assets_m       as numeric(18, 2))         as net_assets_m,
        cast(member_accounts    as bigint)                 as member_accounts,

        -- fund type
        lower(trim(fund_type))                             as fund_type,   -- industry / retail / public sector

        -- audit
        _loaded_at,
        _run_date

    from source
    where fund_name is not null
      and quarter_year is not null
)

select * from cleaned
