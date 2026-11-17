# BigQuery setup

The API reads three tables from one dataset (default `marketing_reporting`).

## Tables from the pipeline

`rpt_paid_media_campaign_daily` and `rpt_channel_performance_daily` are produced by
[marketing-data-integration-pipeline](https://github.com/Guri-L/marketing-data-integration-pipeline).
Run it with `WAREHOUSE=bigquery` and the tables appear in `marketing_reporting`.

The API uses only the columns listed in [design.md](design.md#table-contract); extra columns
are ignored.

## Budgets table

`campaign_budgets` is not produced by the pipeline. Create it once and maintain it monthly:

```sql
CREATE TABLE IF NOT EXISTS `your-project.marketing_reporting.campaign_budgets` (
  campaign_id STRING NOT NULL,
  month       DATE   NOT NULL,   -- first day of the month
  budget      FLOAT64 NOT NULL   -- account currency
);

INSERT INTO `your-project.marketing_reporting.campaign_budgets` (campaign_id, month, budget)
VALUES ('20418830011', DATE '2026-10-01', 3480),
       ('20418830012', DATE '2026-10-01', 9010);
```

A campaign without a budget row for the month is left out of `/v1/pacing`. A budget row for a
campaign with no spend yet is reported with zero spend and status `under`.

## Access

Create a service account for the API and grant it the minimum:

```bash
gcloud iam service-accounts create marketing-api --display-name "Marketing reporting API"

# Read the reporting dataset only
bq add-iam-policy-binding \
  --member "serviceAccount:marketing-api@your-project.iam.gserviceaccount.com" \
  --role roles/bigquery.dataViewer \
  your-project:marketing_reporting

# Run query jobs
gcloud projects add-iam-policy-binding your-project \
  --member "serviceAccount:marketing-api@your-project.iam.gserviceaccount.com" \
  --role roles/bigquery.jobUser
```

The API never writes. It should not have `dataEditor` on anything, and it should not be able
to read the raw or staging datasets.

## Cost

Every query sets `maximum_bytes_billed` (`BQ_MAX_BYTES_BILLED`, default 1 GB). The pipeline
partitions the reporting tables by `date`, and every query here filters on `date`, so scans
are limited to the requested period. With the 5-minute cache, a dashboard that refreshes
often issues one query per distinct request per 5 minutes.

These commands are written from the documentation and have not been run for this project.
