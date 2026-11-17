# BigQuery validation

Date: 2026-11-17. Location: `US`. Dataset: `marketing_reporting`.

## What was run

The dataset holds a two-week slice (2026-10-12 to 2026-10-25, 140 campaign-days) of the same
synthetic data as the seed files, loaded during validation of the upstream pipeline. A
`campaign_budgets` table with the ten October budgets was added for this test.

Each of the eight queries was rendered exactly as the API renders it for BigQuery, with the
bound parameters replaced by literal values of the same type, and executed. The same query
with the same values was run on the local DuckDB backend, restricted to the same dates. For
every query, the row count and the sum of each numeric output column were compared.

## Results

| Query | Parameters used | Rows | Result |
| --- | --- | ---: | --- |
| `data_range` | none | 1 | identical |
| `kpi_summary` | 20 to 25 Oct, previous period from 14 Oct, no platform filter | 2 | identical across 15 columns |
| `kpi_timeseries` | 12 to 25 Oct, weekly, `meta_ads` | 2 | identical across 11 columns |
| `campaigns` | 20 to 25 Oct, search `retarget`, sort `ga4_roas desc`, limit 3 | 2 | identical across 13 columns, including rank, share and total |
| `campaign_daily` | campaign `20418830011`, 20 to 25 Oct | 6 | identical across 9 columns, including the 7-day average |
| `channels` | 20 to 25 Oct | 6 | identical across 11 columns |
| `platforms` | 20 to 25 Oct | 2 | identical across 13 columns |
| `pacing` | as of 25 Oct, 6 days remaining, thresholds 1.10 and 0.85 | 10 | identical across 6 columns and all status counts |

No query needed a change to run in BigQuery.

## What this does not cover

- **Bound parameters through the Python client.** Parameters were replaced by literals because
  the validation ran through a SQL console. `BigQueryWarehouse` builds
  `ScalarQueryParameter` objects, sets `maximum_bytes_billed` and handles errors; that code is
  tested against a fake client only. The first run with real credentials is the remaining check.
- **A NULL filter parameter.** `(@platform is null or platform = @platform)` was validated with
  `CAST(NULL AS STRING)` as the literal. A NULL-valued STRING parameter should behave the
  same, but that exact form has not been executed.
- **The full dataset** and any performance measurement.
