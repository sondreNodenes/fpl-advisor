# CV bullets: fpl-advisor

Every number is followed by the command that reproduces it. Numbers come from `RESULTS.md`
(generated, never hand-edited) or from running the repo.

- Built an end-to-end Fantasy Premier League advisor in Python (FastAPI, pandas,
  scikit-learn, Pyomo): it ingests the public FPL API into parquet snapshots, predicts
  next-gameweek points for every player, and recommends transfers, captain and starting XI
  through a web page, CLI, JSON API and an MCP server for Claude.
- Trained a gradient-boosted points model on four past seasons plus the current one with
  strictly time-ordered evaluation. On the held-out 2025-26 season it cut MAE from 2.157 to
  1.867 and raised per-gameweek Spearman rank correlation from 0.375 to 0.485 against a
  last-3-gameweek baseline, across 13,533 active player-gameweeks
  [`python -m fpl_advisor.evaluate`]. A leakage check showed a widely used community "xP"
  column could not be a fair baseline (Spearman 0.586 on 90-minute starters)
  [`python -m fpl_advisor.evaluate`].
- Formulated FPL squad selection as a mixed-integer program (Pyomo + HiGHS) covering budget,
  2/5/5/3 squad, 3-per-club, formation, captaincy and 4-point transfer hits. It returns
  ranked 0-, 1- and 2-transfer plans with their net expected gain. The optimizer, feature
  leakage, parsers, API and MCP tools are covered by 57 automated tests [`make test`].
- Defined the GCP deployment fully in Terraform (Cloud Run service and scheduled job, Cloud
  Storage, Artifact Registry, least-privilege service accounts) with GitHub Actions CI/CD
  over Workload Identity Federation (no stored keys). Everything scales to zero, with no
  load balancer, NAT or minimum instances. Deployed at
  https://fpl-advisor-7dembcoibq-lz.a.run.app [`make tf-check`; CI workflows `CI` and `Deploy`].

_Not yet claimable: comparison with FPL's `ep_next`. Add it once GW9 has been ingested._
