# Decisions

One line per decision: decision — reason.

- Flat package layout (`fpl_advisor/` at repo root, not `src/`) — matches the brief and `python -m fpl_advisor.*` commands.
- Python pinned to 3.12.x (`>=3.12,<3.13`) — brief specifies 3.12; avoids wheel gaps for Pyomo/HiGHS/LightGBM.
- Dev tools in a uv dependency group (`pytest`, `ruff`) — keeps runtime image lean.
- `make run` points at `fpl_advisor.app:app` before it exists — target is filled in Phase 4.
- Brief saved as `CLAUDE.md` — so future sessions load it automatically.
- Past seasons from the MIT-licensed `vaastav/Fantasy-Premier-League` dataset (2022-23..2025-26 `merged_gw.csv`, plain HTTPS download) — far more training data than 7 gameweeks, and its `xP` column gives a historical FPL expected-points baseline the official API does not keep.
- Current-season history from `element-summary/` only for players with >0 season minutes — the rest are all-zero rows; saves ~200 requests per run.
- Opponent strength will be derived from results (rolling goals for/against), not `teams.strength_*` — the API returns 0 for attack/defence strength this season.
- `free_transfers` is estimated from `entry/{id}/history` (1/GW, roll over to max 5, wildcard/free hit keep count) — the API does not expose it.
- Snapshots are keyed on the last gameweek with `finished` and `data_checked` both true — bonus points are final only then.
- Model predicts points per fixture; gameweek points = sum over the player's fixtures — blanks give 0 and doubles two terms without special-casing.
- Added a 1-GW window to form features — best signal for "will he play"; active-player MAE 1.886 → 1.854 on 2025-26 (scratch experiment, not in repo).
- Kept HistGradientBoosting; LightGBM tied it on 2025-26 (active Spearman 0.486 vs 0.485), so no extra dependency.
- Squared-error loss, not absolute — the optimizer needs expected points (mean), not a median that is 0 for most players.
- Community `xP` dropped as the ep baseline — it ranks 90-minute starters at Spearman 0.59, which only post-deadline information explains (check is reproduced in `evaluate`). `ep_next` baseline comes from our own pre-deadline snapshots instead and fills in from GW9.
- Availability applied after the model (`× chance_of_playing_next_round`) — it is not recorded historically, so it cannot be a training feature.
- Kept opponent/team strength features although they add ~nothing over form-only — required by the brief; reported honestly in RESULTS.md.
- Selling prices approximated with current prices — the public picks endpoint has no `selling_price` (only the logged-in `my-team` endpoint does); can overstate budget by up to half of any price rise.
- Optimizer solves once per exact transfer count (0, 1, 2) and ranks plans — gives the user the best option at each count, and ties go to fewer transfers.
- Vice-captain = second-highest expected starter, chosen after solving — it does not change expected points unless the captain blanks.
- Free hit week: advise on the previous gameweek's squad and bank — the free hit squad reverts.
- Added `GET /predictions` (top players by position) — the page needs "predicted points for every player" without returning ~670 rows on every `/recommend`.
- Kept `httpx` for FastAPI's TestClient and silenced Starlette's deprecation warning — the suggested replacement package was not verified, so not installed.
- MCP server uses `mcp` 2.x `MCPServer` (FastMCP was renamed in 2.x) over stdio — what Claude Desktop and Claude Code launch locally.
- GCS bucket mounted into Cloud Run as a volume (Cloud Storage FUSE) instead of a storage SDK — no extra dependency and the code keeps using local paths.
- Snapshots finish with a `_SUCCESS` marker instead of a directory rename — renames are not atomic on GCS FUSE.
- Cloud Scheduler in `europe-west1` — it is not offered in `europe-north1` (checked docs.cloud.google.com/scheduler/docs/locations); it triggers the `europe-north1` job cross-region.
- Two Terraform roots: `bootstrap` (human, once: IAM, WIF, registry, bucket) and `app` (CI) — CI's deployer never needs IAM admin rights.
- `pipeline.py` retrains only when a new snapshot lands — frequent matchday triggers stay cheap (one bootstrap request).
- Terraform 1.5.7 pinned in CI to match the local version the configs were validated with; Google provider ~> 8.0 (lock file 8.8.1).
- GitHub Actions bumped to current majors (checkout v7, setup-uv v10.2.0 (exact tag: the repo has no floating major tags), auth v3, setup-gcloud v3, setup-terraform v4) — fixes the Node 20 deprecation warnings.
