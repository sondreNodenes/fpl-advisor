# Progress

## Done
- Phase 0: scaffold (uv, ruff, pytest, Makefile, CI).
- Phase 1: `api.py` client (retries, cache, 1 s spacing), parsers, `ingest.py` snapshots + past seasons.
- Phase 2: `features.py` (per-fixture, leakage-safe), `model.py` (HistGB), `evaluate.py` → RESULTS.md.
- Phase 3: `optimize.py` Pyomo/HiGHS MILP for 0–2 transfers, XI, captain.
- Phase 4: `advisor.py` service, FastAPI `app.py` + static page, `cli.py`.
- Phase 5: `mcp_server.py` with 4 tools, README setup docs.
- Phase 6: Terraform (bootstrap + app), Dockerfile, `pipeline.py`, deploy workflow; CI validates Terraform.
- Phase 7: README (architecture, results, limitations), CV_BULLETS.md.

## Next
- Human steps in `BLOCKERS.md` (gcloud login, project, billing), then `make bootstrap`, set GitHub variables, deploy, `make run-job`.
- After GW9 is ingested: re-run `make evaluate` to get the first `ep_next` comparison; update README and CV bullets.
- Add `docs/screenshot.png`.

## Known issues
- Docker image not built locally (Docker daemon was not running); first build happens in the deploy workflow.
- `ep_next` baseline has no rows until a pre-deadline snapshot is followed by that gameweek finishing (first: GW9).
- Selling prices approximated with current prices.
