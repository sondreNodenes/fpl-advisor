# fpl-advisor

Fantasy Premier League advisor. Paste an FPL team URL or ID and get predicted points for every player
for the next gameweek, plus recommended transfers (0 to 2, net of the 4-point hit), captain,
vice-captain and the best starting XI.

> Work in progress. See `PROGRESS.md` for status.

## What it does

_TBD (Phase 4)._

## Architecture

_TBD (Phase 6)._

## Run locally

```bash
uv sync
make test
make lint
```

## Use from Claude (MCP server)

`fpl_advisor/mcp_server.py` exposes four tools over stdio: `get_team(team)`,
`predict_player(name)`, `recommend_transfers(team)` and `captain_options(team)`.
It needs local data and a model first (`python -m fpl_advisor.ingest && python -m fpl_advisor.model`).

Claude Code:

```bash
claude mcp add fpl-advisor -- uv run --directory /path/to/fpl-advisor python -m fpl_advisor.mcp_server
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "fpl-advisor": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/fpl-advisor", "python", "-m", "fpl_advisor.mcp_server"]
    }
  }
}
```

Then ask, for example, "Who should team 5767275 captain this week?".

## Deploy (GCP)

All infrastructure is Terraform in `infra/`, split in two roots:

- `infra/bootstrap` (applied once by you): APIs, Artifact Registry, data bucket, service
  accounts with least privilege, and Workload Identity Federation for GitHub Actions
  (only `main` of this repo can deploy; no JSON keys).
- `infra/app` (applied by `.github/workflows/deploy.yml` on every push to `main`): Cloud Run
  service (public, scale to zero), Cloud Run job `fpl-pipeline` (ingest + retrain), and two
  Cloud Scheduler triggers (nightly 03:00 UTC, and 19:00/22:00 UTC Friday to Monday).

The data bucket is mounted into Cloud Run as a volume (read-only for the service,
read-write for the job), so the code only ever reads and writes local paths.

Steps (also in `BLOCKERS.md`):

```bash
gcloud auth login && gcloud auth application-default login
gcloud config set project <project-id>
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com
make bootstrap                      # state bucket <project-id>-tfstate + infra/bootstrap
gh variable set GCP_PROJECT_ID --body <project-id>
gh variable set GCP_WIF_PROVIDER --body "$(terraform -chdir=infra/bootstrap output -raw workload_identity_provider)"
gh variable set GCP_DEPLOY_SA --body "$(terraform -chdir=infra/bootstrap output -raw deployer_service_account)"
gh workflow run deploy.yml          # build, push, terraform plan + apply, smoke test /health
make run-job                        # first data load (~10 min)
```

### Cost

Designed to stay at or near 0 USD/month. Nothing bills while idle: no load balancer, NAT,
GKE or minimum instances.

| Resource | Usage | Expected cost |
|---|---|---|
| Cloud Run service | scale to zero, low traffic | free tier |
| Cloud Run job | ~80 runs/month, most under a minute; ~5 runs of ~10 min after a gameweek | free tier |
| Cloud Scheduler | 2 jobs (3 free per billing account) | free |
| Cloud Storage | ~10 MB parquet + model | < 0.01 USD |
| Artifact Registry | 3 images kept (cleanup policy) | ~0.10 USD if above the 0.5 GB free tier |

These are estimates from Google's published free tiers, not measured bills. Set a 5 USD budget alert.
`make destroy` removes everything Terraform created (the state bucket is kept).

## Results

_TBD (Phase 2). All numbers will be produced by `python -m fpl_advisor.evaluate`._

## Limitations

_TBD._
