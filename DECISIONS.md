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
