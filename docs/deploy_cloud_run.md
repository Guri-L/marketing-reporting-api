# Deploying to Cloud Run

This is a guide, not a tested script: the commands below have not been run for this project.
Cloud Run fits because the service is stateless, reads `PORT` from the environment, starts in
about a second and handles `SIGTERM`.

Set these first:

```bash
export PROJECT=your-project REGION=europe-west1 SERVICE=marketing-reporting-api
```

## 1. Build and push the image

```bash
gcloud artifacts repositories create apis --repository-format docker --location $REGION
gcloud builds submit --tag $REGION-docker.pkg.dev/$PROJECT/apis/$SERVICE:0.1.0
```

## 2. Store the API keys as a secret

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))" | \
  gcloud secrets create marketing-api-keys --data-file=-

gcloud secrets add-iam-policy-binding marketing-api-keys \
  --member "serviceAccount:marketing-api@$PROJECT.iam.gserviceaccount.com" \
  --role roles/secretmanager.secretAccessor
```

For several consumers, store a comma-separated list. To rotate a key, add the new one, move
consumers over, then remove the old one; each change is a new secret version and a new revision.

## 3. Deploy

The service account is the one from [bigquery_setup.md](bigquery_setup.md).

```bash
gcloud run deploy $SERVICE \
  --image $REGION-docker.pkg.dev/$PROJECT/apis/$SERVICE:0.1.0 \
  --region $REGION \
  --service-account marketing-api@$PROJECT.iam.gserviceaccount.com \
  --set-env-vars WAREHOUSE=bigquery,GCP_PROJECT_ID=$PROJECT,BQ_DATASET=marketing_reporting,BQ_LOCATION=US \
  --set-secrets API_KEYS=marketing-api-keys:latest \
  --memory 512Mi --cpu 1 --concurrency 40 \
  --min-instances 0 --max-instances 3 \
  --allow-unauthenticated
```

`--allow-unauthenticated` makes the URL reachable; the API's own key check still applies. For
an internal-only service, drop that flag and let Cloud Run IAM authenticate callers as well.

On Cloud Run the service uses the attached service account through Application Default
Credentials. No key file is created or mounted.

## 4. Check it

```bash
URL=$(gcloud run services describe $SERVICE --region $REGION --format 'value(status.url)')
curl -s $URL/ready
curl -s -H "X-API-Key: <your key>" "$URL/v1/meta"
```

## Things to know before relying on it

- **Rate limits and cache are per instance.** With `--max-instances 3`, a key can make up to
  three times `RATE_LIMIT_PER_MINUTE`. Set `--max-instances 1` for a strict limit, or move the
  limiter to a shared store.
- **Cold starts.** With `--min-instances 0` the first request after idle waits for a
  container. Set `--min-instances 1` if a dashboard must always be fast.
- **Alerting.** Logs are JSON, so a log-based alert on `severity>=ERROR` or on
  `jsonPayload.status>=500` works without extra setup.
- **Location.** `BQ_LOCATION` must match the dataset's location, or queries fail with a
  "dataset not found" error.
