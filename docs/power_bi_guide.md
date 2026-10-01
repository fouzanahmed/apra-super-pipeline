# Power BI dashboard: step by step

Builds a 3-page report on the dbt marts in the local Postgres database.

| Page | Question it answers | Source table |
|---|---|---|
| 1. Fund rankings | Who has the best 5-year returns? | `mart.fund_performance` |
| 2. Fee vs return | Who is good value for money? | `mart.fee_vs_return` |
| 3. Trends | How have fees and returns moved since 2020? | `mart.fund_performance` |

`mart.member_flow` is skipped for now: the source file has almost no member-account data.

---

## 0. Before you start

1. Start **Docker Desktop**, then run `docker start apra-postgres`.
2. The marts must exist. If in doubt, rerun `dbt run` (see the README quick start).

Power BI connects as a **read-only** user, so a dashboard can never change data:

| Setting | Value |
|---|---|
| Server | `localhost:5432` |
| Database | `superannuation` |
| User | `powerbi_reader` |
| Password | `powerbi_local` (local development only) |

The user was created with the SQL below. It is already done locally; rerun it on any new database (e.g. RDS):

```sql
create role powerbi_reader login password '<choose one>';
grant connect on database superannuation to powerbi_reader;
grant usage on schema mart to powerbi_reader;
grant select on all tables in schema mart to powerbi_reader;
-- dbt drops and recreates mart tables every run; this keeps the grant on the new ones
alter default privileges for role pipeline_user in schema mart grant select on tables to powerbi_reader;
```

---

## 1. Install Power BI Desktop

Microsoft Store, then search **Power BI Desktop**, then **Get**. It is free, Windows only, and updates itself.

---

## 2. Connect to Postgres

1. Open Power BI Desktop, then **Blank report**.
2. **Home → Get data → More… → Database → PostgreSQL database → Connect**.
3. Server: `localhost:5432`, Database: `superannuation`.
4. **Data Connectivity mode: Import.** Import copies the data into the report, which is fast and works offline. (DirectQuery queries the database live on every click; use it later against RDS if you want "always live".)
5. **OK**, then choose the **Database** tab on the left of the login box, then enter user `powerbi_reader` / `powerbi_local`, then **Connect**.
6. If you see *"unable to connect using an encrypted connection"*, click **OK** to connect without encryption. The local Docker Postgres has no SSL certificate. (RDS does, so leave encryption on there.)
7. In the Navigator, tick `mart.fund_performance` and `mart.fee_vs_return`, then click **Load**.

**Check:** the **Data** pane on the right lists both tables. `fund_performance` has 575 rows and `fee_vs_return` has 35 (hover the table in Table view to see the count).

---

## 3. Prepare the data model (once)

### 3a. Show percentages as percentages
Returns and fees are stored as decimals (0.0852 = 8.52%).
Go to **Table view** (grid icon, left). For each of `return_1yr`, `return_3yr`, `return_5yr`, `total_fee_pct`, `median_fee`, `median_return`, `fee_percentile`: click the column, then **Column tools → Format → Percentage**, with **2** decimal places.

Also check that `quarter_date` has **Data type = Date** (not Date/Time).

### 3b. A readable label per product
Some funds have several MySuper products (e.g. HESTA). Without this column, a chart would merge them into one averaged bar.

Select `fund_performance`, then **Table tools → New column**, and paste:

```DAX
Fund Label =
VAR products_this_quarter =
    CALCULATE (
        DISTINCTCOUNT ( fund_performance[product_name] ),
        ALLEXCEPT ( fund_performance, fund_performance[abn], fund_performance[quarter_date] )
    )
RETURN
    IF (
        products_this_quarter > 1,
        fund_performance[fund_name] & " – " & fund_performance[product_name],
        fund_performance[fund_name]
    )
```

Repeat on `fee_vs_return`, replacing every `fund_performance` with `fee_vs_return` and removing `, fee_vs_return[quarter_date]` (that table has a single quarter).

*What it does:* for each row, it counts how many products the same fund (ABN) has that quarter. If more than one, the product name is added to the label.

### 3c. Flag the latest quarter
On `fund_performance`, click **New column**:

```DAX
Is Latest Quarter = fund_performance[quarter_date] = MAX ( fund_performance[quarter_date] )
```

In a calculated column, `MAX` looks at the whole table, so this is TRUE for 30 Sep 2023 rows only.

### 3d. Measures (calculations that respond to filters)
On `fund_performance`, click **New measure** for each of these, and format each as a percentage:

```DAX
Avg 5yr Return = AVERAGE ( fund_performance[return_5yr] )
```
```DAX
Avg Total Fee = AVERAGE ( fund_performance[total_fee_pct] )
```
```DAX
Asset-weighted Fee =
DIVIDE (
    SUMX ( fund_performance, fund_performance[total_fee_pct] * fund_performance[net_assets_m] ),
    SUM ( fund_performance[net_assets_m] )
)
```

*Why two fee averages?* `Avg Total Fee` treats a $0.2bn fund the same as a $200bn fund. `Asset-weighted Fee` is what the average member's dollar actually pays. Showing both on page 3 is a good interview talking point.

---

## 4. Page 1: Fund rankings

1. Rename the page (double-click the tab) to **Fund rankings**.
2. **Clustered bar chart**:
   - Y-axis: `fund_performance[Fund Label]`
   - X-axis: `Avg 5yr Return`
   - Filters pane → **Filters on this visual**:
     - `Is Latest Quarter` = True
     - `Fund Label` → Filter type **Top N** → Show items **Top 20** → By value: `Avg 5yr Return` → **Apply filter**
   - Sort: `…` on the visual → **Sort axis → Avg 5yr Return**, descending.
   - Format → **Data labels: On**.
3. **Slicer**: field `fund_type`, style **Tile** (Format → Slicer settings). Click "industry" to filter the chart to industry funds.
4. **Card**: field `Avg Total Fee`, title "Average fee". Add a second card with `fund_performance[Fund Label]` → **Count (Distinct)**, titled "Products".
5. **Title**: Insert → Text box: "Top 20 MySuper products by 5-year return (Sep 2023)".

**Check:** Meat Industry Employees Super (6.39%) and HOSTPLUS (6.38%) are at the top with no slicer selected.

---

## 5. Page 2: Fee vs return

1. New page (the **+** at the bottom), named **Fee vs return**.
2. **Scatter chart** (source `fee_vs_return`):
   - X-axis: `total_fee_pct` → set aggregation to **Average** (dropdown on the field)
   - Y-axis: `return_1yr` → **Average**
   - Values (a.k.a. Details): `Fund Label`, so each product is one dot
   - Legend: `value_quadrant`
   - Size (optional): `net_assets_m` → Sum, so bigger funds get bigger bubbles
3. **Median lines** make the quadrants visible. Select the chart, then open the **Analytics** pane (magnifier icon in Visualizations), then:
   - **X-Axis median line → Add**
   - **Y-Axis median line → Add**
4. Colours: Format → Markers → Colors: best_value = green, worst_value = red, the other two = grey/amber.
5. **Table** beside it: `Fund Label`, `value_quadrant`, `total_fee_pct`, `return_1yr`. Click a dot on the scatter and the table filters to it (cross-filtering).

**Check:** 10 dots are `best_value` and 8 are `worst_value`, matching the SQL answers in the API.

*Talking point:* value here is based on the **1-year** return, so one good or bad year moves a fund between quadrants.

---

## 6. Page 3: Trends

1. New page, named **Trends**.
2. **Line chart**:
   - X-axis: `quarter_date`. In the field dropdown pick **quarter_date** (not *Date Hierarchy*), so every quarter shows.
   - Y-axis: `Avg Total Fee` and `Asset-weighted Fee`
3. **Line chart** below it:
   - X-axis: `quarter_date` (not hierarchy)
   - Y-axis: `Avg 5yr Return`
   - Legend: `fund_type`
4. **Slicer**: `fund_name` with search on (Format → Slicer settings → Search), so viewers can pick e.g. AustralianSuper and Hostplus.

**Check:** with the AustralianSuper filter, the fee line falls from about 0.23% (2020–21) to about 0.18% (2023).

---

## 7. Save, refresh, share

- **Save** as `powerbi/apra_super_dashboard.pbix` in the repo.
- **After new data** (rerun of ingestion + `dbt run`): **Home → Refresh**.
- **Share (optional):** **Home → Publish** to Power BI Service (needs a free work or school account). A service-hosted report can't see `localhost`, so live refresh from the web needs the RDS database or an on-premises data gateway. For a portfolio, screenshots of each page in the README also work.
