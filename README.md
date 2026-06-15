# SQL Analytics Portfolio

Multi-domain SQL analysis project demonstrating intermediate-to-advanced query patterns
across 4 real datasets. All queries written and executed against live PostgreSQL databases.

## Business Questions Answered

### game_jet — Mobile Game Monetization
**Question:** What is the revenue distribution across player spending tiers, and when do players convert?

- `01_user_persona_segmentation.sql` — Segments 22,576 players into free/minnow/dolphin/whale tiers using CTEs and CASE
- `02_revenue_by_persona.sql` — Reveals 210 whales (9% of paying users) generate $4M (52% of revenue)
- `03_conversion_window.sql` — Shows 83% of converting players make their first purchase within 3 days of install

### intel — Device Repurposing Environmental Impact
**Question:** Which device types and regions contribute most to energy savings and CO2 reduction?

- `01_device_impact_by_region.sql` — Window functions calculate each device type's % contribution to regional totals

### hover — Roofing Jobs and Weather Correlation
**Question:** Does weather event frequency correlate with roofing job volume by state and week?

- `01_jobs_weather_correlation.sql` — Joins job and weather data on state + week using DATE_TRUNC for time bucketing

### instacart — Customer Issue Rates
**Question:** Which regions have the highest rate of low-rated orders with reported issues?

- `01_issue_rate_by_region.sql` — Conditional aggregation shows NYC (6.59%) and SF (6.40%) far above Chicago (2.29%)

## SQL Techniques Demonstrated

| Technique | File |
|---|---|
| CTEs (WITH clauses) | game_jet/01, game_jet/02, game_jet/03 |
| CASE statements | game_jet/01, game_jet/03 |
| Window functions (SUM OVER PARTITION BY) | intel/01 |
| Conditional aggregation (SUM CASE WHEN) | instacart/01 |
| Multi-table JOINs | all files |
| Subqueries | hover/01 |
| Date functions (DATE_TRUNC, date arithmetic) | game_jet/03, hover/01 |
| Chained CTEs | game_jet/03 |

## Environment
- PostgreSQL via SQLPad
- University of Arizona sql_course database
- Executed July 2025
