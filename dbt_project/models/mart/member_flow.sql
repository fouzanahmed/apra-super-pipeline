with quarterly as (
    select
        fund_name,
        abn,
        fund_type,
        quarter_date,
        member_accounts,
        net_assets_m,
        lag(member_accounts) over (partition by abn order by quarter_date) as prev_member_accounts,
        lag(net_assets_m)    over (partition by abn order by quarter_date) as prev_net_assets_m
    from {{ ref('stg_apra_mysuper') }}
    where member_accounts is not null
),

with_flows as (
    select
        fund_name,
        abn,
        fund_type,
        quarter_date,
        member_accounts,
        net_assets_m,
        member_accounts - prev_member_accounts                                          as member_net_change,
        round(
            100.0 * (member_accounts - prev_member_accounts) / nullif(prev_member_accounts, 0),
            2
        )                                                                               as member_growth_pct,
        net_assets_m    - prev_net_assets_m                                             as asset_net_change_m,

        -- rolling 4-quarter sum of member change (annual trend)
        sum(member_accounts - prev_member_accounts)
            over (partition by abn order by quarter_date rows between 3 preceding and current row)
                                                                                        as rolling_annual_member_change
    from quarterly
    where prev_member_accounts is not null
)

select * from with_flows
