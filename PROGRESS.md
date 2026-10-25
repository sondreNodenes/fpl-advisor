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
- Deployed 2026-10-25 to project `fpl-advisor-snf`: https://fpl-advisor-7dembcoibq-lz.a.run.app (health, /recommend, page verified; job run succeeded).

- 2026-10-25: 3-gameweek transfer planning, per-swap reasons, chip advice (`chips.py`), dashboard page; screenshot in `docs/`.

- Security pass (SECURITY_BRIEF.md): phase 0 threat model, phase 1 baseline scans (`make security`, `security.yml`, `security/baseline/`), phase 2 app hardening (`security.py`: rate limit, body limit, work limiter, headers/CSP; strict team parsing; safe outbound client), phase 3 container/CI hardening (multi-stage digest-pinned image, SHA-pinned actions, Dependabot, deploy image gate, WIF pinned to repo/owner IDs), phase 4 cloud hardening (bucket versioning/lifecycle, deployer `run.developer` + custom role, default compute Editor removed, max 1 instance, gzip, global per-IP limit, measured worst-case cost) — bootstrap apply pending (BLOCKERS.md), phase 5 proof (`make security` strict incl. abuse tests, `security/after/`, README Security section).

## Next
- After GW9 is ingested: re-run `make evaluate` to get the first `ep_next` comparison; update README and CV bullets.

## Known issues
- Initial data was copied from the local machine to the bucket; the job has not yet written a snapshot on GCS FUSE itself (first chance: after GW8 is final).
- Local `gcloud run ...` commands fail (`No module named 'grpc'`); fix with `gcloud components reinstall`. The REST API and Terraform work.
- `ep_next` baseline has no rows until a pre-deadline snapshot is followed by that gameweek finishing (first: GW9).
- Selling prices approximated with current prices.
