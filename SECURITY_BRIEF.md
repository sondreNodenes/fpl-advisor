# Security brief

The security pass for this repo follows the brief the owner supplied on 2026-10-25 ("Prompt 2:
FPL Advisor, security pass"): threat model, baseline scans in CI, application, container, CI
and cloud hardening with before/after evidence, a reproducible `make security`, and learning
material. Scope: this repo and the owner's GCP project `fpl-advisor-snf` only.

Phases: 0 threat model, 1 baseline scans, 2 application hardening, 3 container and CI
hardening, 4 cloud and Terraform hardening, 5 prove it, 6 learning material.
Progress is tracked in `PROGRESS.md`, choices in `DECISIONS.md`, findings in
`SECURITY_FINDINGS.md`.
