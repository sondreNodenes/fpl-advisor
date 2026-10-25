# FPL Advisor: brief for Claude Code

Paste this whole file as the first message in an empty repo (or save it as `CLAUDE.md` and say "Read CLAUDE.md and start with Phase 0").

## 0. Working rules (read first)

**Autonomy**
- Do not ask me questions. When a choice is needed, pick the sensible default, write one line in `DECISIONS.md` (decision + reason), and continue.
- Only stop and write `BLOCKERS.md` if something needs my hands (for example `gcloud auth login`, a browser consent screen, billing). Keep working on everything that is not blocked.
- Work phase by phase. Finish a phase completely (code, tests, docs, commit) before the next. Commit after each phase with a clear message.

**Token efficiency**
- Plan once per phase in at most 10 lines, then execute. No long explanations in chat. A phase summary is 5 lines max.
- Keep `PROGRESS.md` (done / next / known issues) up to date so you never need to re-read the whole repo.
- Prefer targeted edits over rewriting files. Do not re-read files you just wrote. Run tests quietly (`pytest -q`, `terraform validate`) and only look at failures.
- Do not add dependencies, abstractions or features that are not in this brief.

**Quality bar (this is a portfolio project that recruiters will read)**
- Small, clear modules. Type hints. `ruff` for lint and format. Tests for the logic that matters (parsing, features, optimizer constraints).
- Never invent numbers. Every metric in `README.md` and `RESULTS.md` must come from a command in the repo that I can re-run. If a result is weak, report it honestly.
- README in English with: what it does, architecture diagram (mermaid), how to run locally, how to deploy, results table, limitations.

## 1. What we are building

A Fantasy Premier League advisor. The user pastes an FPL team URL or ID, for example `https://fantasy.premierleague.com/en/entry/5767275/event/8`, and gets:
1. Predicted points for every player for the next gameweek.
2. Recommended transfers (0 to 2, net of the 4-point hit), captain and vice-captain, and the best starting XI for their current squad.

Data comes from the public, unofficial FPL JSON API. It must be refreshed automatically after matches.

Tech decisions (already made, do not revisit):
- Python 3.12, `uv` for dependencies, FastAPI, pandas, scikit-learn (and LightGBM only if it clearly beats scikit-learn's HistGradientBoosting), Pyomo with HiGHS (fall back to GLPK) for optimization.
- GCP in region `europe-north1`: Cloud Run (service + job), Cloud Scheduler, Cloud Storage (parquet and model files), Artifact Registry. No GKE, no BigQuery, no Cloud SQL (cost).
- Terraform for all infrastructure. GitHub Actions for CI/CD, authenticated to GCP with Workload Identity Federation (no JSON keys in the repo).
- Public GitHub repo named `fpl-advisor`.

Test team for development: team ID `5767275`, current gameweek 8 when this was written.

## 2. FPL API notes (verify each endpoint with one request before relying on it)

Base: `https://fantasy.premierleague.com/api/`. No auth for public endpoints. Send a normal `User-Agent`, cache responses, and wait at least 1 second between calls. Be a polite client.

- `bootstrap-static/`: `elements` (players: `now_cost` in tenths of millions, `form`, `total_points`, `minutes`, `status`, `chance_of_playing_next_round`, `expected_goals`, `expected_assists`, `ict_index`, `ep_next`, `team`, `element_type`), `teams` (with strength ratings), `events` (gameweeks, `is_current`, `is_next`, deadlines), `element_types`.
- `fixtures/`: all fixtures with `event`, `team_h`, `team_a`, difficulty fields.
- `element-summary/{player_id}/`: `history` (per-gameweek stats this season) and `fixtures`.
- `entry/{team_id}/`: manager info. `entry/{team_id}/history/`: past gameweeks, chips, bank.
- `entry/{team_id}/event/{gw}/picks/`: the 15 picks, captain flags, `entry_history` (bank, value, transfers).
- `event/{gw}/live/`: live and final points per player for a gameweek.

URL parsing: accept a raw integer ID or any URL containing `/entry/{id}`. The gameweek in the URL is optional; default to the current gameweek from `bootstrap-static`.

Edge cases to handle: API temporarily unavailable (return a clear error), private or deleted team, gameweek in progress (use the last finished gameweek for features, predict the next), blank and double gameweeks (a player can have 0 or 2 fixtures).

## 3. Phases

### Phase 0: Scaffold (small)
- `uv init`, package `fpl_advisor/`, `tests/`, `ruff`, `pytest`, `Makefile` (`make test`, `make lint`, `make run`).
- GitHub Actions workflow: lint and test on every push.
- `README.md` skeleton, `DECISIONS.md`, `PROGRESS.md`.
- Acceptance: `make test` and CI pass on an empty-but-structured project.

### Phase 1: Data layer
- `fpl_advisor/api.py`: thin client with retries, caching to `data/cache/`, and typed parsing of the endpoints above.
- `fpl_advisor/ingest.py`: snapshot each finished gameweek to `data/snapshots/gw=NN/*.parquet` (players, per-gameweek player history, fixtures, teams). Idempotent: running twice does not duplicate.
- History for training: build per-player per-gameweek rows from `element-summary` for the current season. If a public community dataset of past seasons is downloadable without scraping tricks or breaking terms, add it as an optional source and note it in `DECISIONS.md`. Otherwise train on the current season only and say so in the README.
- Acceptance: `python -m fpl_advisor.ingest` produces parquet files; tests cover parsing with saved fixture JSON (store small samples in `tests/data/`).

### Phase 2: Model
- Target: points in the next gameweek. Features: rolling form (last 3 and 5 gameweeks of points, minutes, expected goals and assists, ICT), opponent strength, home or away, price, position, availability. Handle zero-minute players and double gameweeks.
- Baselines to beat, reported side by side: (a) last-3-gameweek average points, (b) FPL's own `ep_next`, (c) a model with only form features.
- Evaluation: time-ordered split only (never random). Report MAE and Spearman rank correlation per position in `RESULTS.md`, generated by `python -m fpl_advisor.evaluate`. If the model does not beat the baselines, say so plainly.
- Save the model to `models/` (gitignored) and to GCS in deployment.
- Acceptance: `evaluate` prints a table and writes `RESULTS.md`; unit tests cover feature building (no leakage: features for gameweek N only use data up to N-1).

### Phase 3: Optimizer
- `fpl_advisor/optimize.py` with Pyomo. Given predicted points and the user's current squad, bank and free transfers, choose up to 2 transfers, the starting XI and the captain to maximize expected points minus 4 per extra transfer.
- Constraints: 15-man squad (2 GK, 5 DEF, 5 MID, 3 FWD), budget using selling prices (the picks and history endpoints give value and bank; if selling price is not available, approximate with current price and say so), max 3 players per club, valid XI formation (1 GK, at least 3 DEF, at least 2 MID, at least 1 FWD), captain scores double.
- Output: ranked recommendations with expected gain and a one-line reason each ("+2.1 expected points over next gameweek, includes -4 hit").
- Acceptance: tests that each constraint holds on random inputs and that 0 transfers is returned when no swap gives a net gain.

### Phase 4: Interface
- FastAPI app: `POST /recommend` with `{ "team": "<url or id>", "gameweek": optional }`, `GET /health`. Pydantic models. Clear error messages.
- One static HTML page served by the app: input box, "Get advice" button, readable result tables. No JS framework.
- CLI: `python -m fpl_advisor.cli "<url or id>"`.
- Acceptance: running it locally with the test team returns predictions and recommendations end to end.

### Phase 5: MCP server
- `fpl_advisor/mcp_server.py` using the official Python MCP SDK. Tools: `get_team(team)`, `predict_player(name)`, `recommend_transfers(team)`, `captain_options(team)`.
- Docs in README showing how to add it to Claude Desktop or Claude Code.
- Acceptance: tools return structured results; one test per tool with mocked data.

### Phase 6: Infrastructure (Terraform)
- `infra/` with: Artifact Registry, Cloud Storage bucket, Cloud Run service (public, scale to zero, `min_instances = 0`, small memory), Cloud Run job for ingest and retrain, Cloud Scheduler to run the job (nightly, plus a more frequent trigger on Fri to Mon evenings UTC), service accounts with least privilege, Workload Identity Federation for GitHub Actions.
- Variables for `project_id`, `region` (default `europe-north1`). No secrets in the repo. Remote state in a GCS bucket created by a bootstrap step documented in the README.
- GitHub Actions deploy workflow: on push to `main`, build image, push, `terraform apply` for the app, deploy to Cloud Run. Manual approval not needed, but `terraform plan` output must be visible in the logs.
- Cost guardrails: document in README the expected monthly cost (should be close to zero), and add a `make destroy` target. Do not create any resource that bills while idle (no load balancers, no always-on instances, no GKE, no NAT).
- Acceptance: `terraform validate` and `terraform fmt -check` pass in CI; if I have run the auth steps in section 4, `terraform apply` works and the Cloud Run URL serves `/health`.

### Phase 7: Polish for the CV
- README: architecture diagram, results table copied from `RESULTS.md`, screenshot placeholder, limitations (unofficial API, small training window), "What I would do next".
- `CV_BULLETS.md`: write 3 to 4 CV bullets for this project using only measured facts from `RESULTS.md` and the repo (components built, metrics, what runs where). Mark every number with the command that produces it.

## 4. What the human (me) does before Phase 6

Claude Code, put this in `BLOCKERS.md` only when you reach Phase 6 and these are not done. Everything before Phase 6 works locally without GCP.

1. Install: `gcloud`, `terraform`, `docker`, `gh`, `uv`.
2. `gcloud auth login` and `gcloud auth application-default login`.
3. Create or choose a GCP project and note the ID. Link a billing account. Create a billing budget with alerts at 50 percent and 100 percent of 5 USD per month.
4. Enable APIs: `run`, `artifactregistry`, `cloudscheduler`, `storage`, `iam`, `iamcredentials`, `cloudresourcemanager`.
5. `gh auth login`, and create the public repo `fpl-advisor`.

## 5. Out of scope

Authentication and user accounts, a database, mobile app, multi-season stat history beyond what is freely downloadable, chip strategy (wildcard, free hit), and any scraping of sites other than the FPL API.
