# Decisions

One line per decision: decision — reason.

- Flat package layout (`fpl_advisor/` at repo root, not `src/`) — matches the brief and `python -m fpl_advisor.*` commands.
- Python pinned to 3.12.x (`>=3.12,<3.13`) — brief specifies 3.12; avoids wheel gaps for Pyomo/HiGHS/LightGBM.
- Dev tools in a uv dependency group (`pytest`, `ruff`) — keeps runtime image lean.
- `make run` points at `fpl_advisor.app:app` before it exists — target is filled in Phase 4.
- Brief saved as `CLAUDE.md` — so future sessions load it automatically.
