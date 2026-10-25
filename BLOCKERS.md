# Blockers (need a human)

Everything up to Phase 6 runs locally. Deployment needs these steps, in order.
Checked on 2026-10-25: `gcloud`, `terraform`, `docker`, `gh`, `uv` are installed, but:

1. **gcloud credentials expired.** Run `gcloud auth login` and `gcloud auth application-default login`.
2. **Choose the GCP project.** The current gcloud project is `optimal-shard-332612`; confirm it or
   create a new one (`gcloud projects create <id>`), then `gcloud config set project <id>`.
3. **Billing.** Link a billing account and create a budget with alerts at 50% and 100% of 5 USD/month
   (Console → Billing → Budgets & alerts).
4. **Enable the API Terraform needs to enable the rest:**
   `gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com`
   (the bootstrap Terraform enables run, artifactregistry, cloudscheduler, storage, iam,
   iamcredentials and sts).
5. **Bootstrap:** `make bootstrap` (creates the `<project>-tfstate` bucket, then applies `infra/bootstrap`).
6. **GitHub variables** from the bootstrap outputs:
   ```bash
   gh variable set GCP_PROJECT_ID --body "<project id>"
   gh variable set GCP_WIF_PROVIDER --body "$(terraform -chdir=infra/bootstrap output -raw workload_identity_provider)"
   gh variable set GCP_DEPLOY_SA --body "$(terraform -chdir=infra/bootstrap output -raw deployer_service_account)"
   ```
7. **Deploy:** `gh workflow run deploy.yml` (or push to `main`), then fill the data bucket once with
   `make run-job` (~10 minutes). Until then `/recommend` returns 503 "Model or data not ready".
