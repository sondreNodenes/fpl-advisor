# Blockers (need a human)

## Open: apply the security changes to the foundation (`infra/bootstrap`)

GitHub Actions can only change `infra/app`; these need your owner rights. The plan was
checked read-only on 2026-10-25: **5 to add, 2 to change, 2 to destroy**:
WIF provider condition (repo/owner IDs), deployer binding replaced (by `repository_id`),
custom role `fplDeployerRunIam` + its binding, `run.developer` added and `run.admin`
removed, Editor removed from the default compute account, data bucket versioning + lifecycle.

1. Lock down the existing Terraform state bucket:

   ```bash
   gcloud storage buckets update gs://fpl-advisor-snf-tfstate --versioning \
     --public-access-prevention --lifecycle-file=infra/state-lifecycle.json
   ```

2. Apply the foundation (review the plan, then type `yes`):

   ```bash
   terraform -chdir=infra/bootstrap init -reconfigure -backend-config="bucket=fpl-advisor-snf-tfstate"
   terraform -chdir=infra/bootstrap apply -var="project_id=fpl-advisor-snf" -var="state_bucket=fpl-advisor-snf-tfstate"
   ```

3. Tell Claude "bootstrap applied". It will then re-run the deploy workflow (proves the
   narrower deployer role and the new WIF condition still work) and record the after state.

Resolved 2026-10-25: gcloud login, project `fpl-advisor-snf`, billing and 55 NOK budget,
first bootstrap apply, GitHub variables.
