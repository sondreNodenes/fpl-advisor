# Progress

## Done
- Phase 0: scaffold (uv, ruff, pytest, Makefile, CI).
- Phase 1: `api.py` client (retries, cache, 1 s spacing), parsers, `ingest.py` snapshots + past seasons.
- Phase 2: `features.py` (per-fixture, leakage-safe), `model.py` (HistGB), `evaluate.py` → RESULTS.md.
- Phase 3: `optimize.py` Pyomo/HiGHS MILP for 0–2 transfers, XI, captain.
- Phase 4: `advisor.py` service, FastAPI `app.py` + static page, `cli.py`.
- Phase 5: `mcp_server.py` with 4 tools, README setup docs.

## Next
- Phase 6: Terraform + deploy workflow.
- Phase 7: README polish, CV_BULLETS.md.

## Known issues
- `ep_next` baseline has no rows until a snapshot taken before a deadline is followed by that gameweek finishing (first: GW9).
- Selling prices approximated with current prices.
