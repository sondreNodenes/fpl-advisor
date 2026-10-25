# Security findings

Every finding comes from a tool report in `security/` or a code review step with a file and
line. Threats and their reasoning are in [THREAT_MODEL.md](THREAT_MODEL.md).

## How to reproduce

```bash
make security                                    # all scans + abuse tests; reports in security/latest/
STRICT=0 security/scan.sh security/baseline      # how the baseline was recorded (report-only)
```

The baseline was recorded with the first version of the script (no abuse-test step and
no `--ignorefile /dev/null`; nothing was ignored then).

`security/scan.sh` needs only Docker and uv. Gitleaks, Trivy and pip-audit run at pinned
versions (images pinned by digest), so a reviewer gets the same tools.

## Baseline (before any fix)

Recorded 2026-10-25 against commit `2d0c06b`; raw reports in `security/baseline/`
(`summary.json` has the counts below).

| Tool | Scope | Result |
|---|---|---|
| gitleaks 8.30.1 | full git history (18 commits) | 0 leaks |
| pip-audit 2.9.0 | `uv.lock` runtime (no-dev) and dev dependencies | 0 known vulnerabilities in both |
| ruff `S` (bandit rules) | all Python | 154: 153 × S101 (asserts in `tests/`), 1 × S607 |
| trivy image 0.76.1 | `python:3.12-slim` (Debian 13.7) packages | 164: 44 HIGH, 58 MEDIUM, 61 LOW, 1 UNKNOWN; 0 with a fix available |
| trivy image | Python packages in the image | 6 (5 MEDIUM, 1 LOW), all in the base image's `pip` 25.0.1, all fixable |
| trivy image | `/usr/bin/uv` binary left in the image | 17 (2 HIGH, 10 MEDIUM, 5 LOW), all fixable |
| trivy config | `infra/` Terraform and `Dockerfile` | 4: 2 MEDIUM, 2 LOW |

Image size (`docker image inspect --format '{{.Size}}'`): 394,508,773 bytes.

## After (hardened)

Recorded 2026-10-25 with `security/scan.sh security/after` (strict mode, all gates pass);
raw reports in `security/after/`. The bootstrap changes (R-09 to R-13, F-07) are in the
Terraform code and pass the IaC scan, but are applied to GCP only after the owner runs the
steps in `BLOCKERS.md`.

| Tool | Before | After | Command |
|---|---|---|---|
| gitleaks (secrets, full history) | 0 | 0 (one false positive allow-listed, see below) | `security/scan.sh` → `gitleaks.json` |
| pip-audit runtime / dev | 0 / 0 | 0 / 0 | → `pip-audit-*.json` |
| ruff `S` (bandit rules) | 154 (153 S101 in tests, 1 S607) | 0 (S101 ignored in `tests/` only; S607 fixed) | `uv run ruff check --select S .` |
| trivy image: OS packages | 164 (44 HIGH, 58 MED, 61 LOW, 1 UNK), 0 fixable | 164 (44 HIGH, 58 MED, 61 LOW, 1 UNK), 0 fixable: accepted (F-03) | → `trivy-image.json` |
| trivy image: Python packages | 6 (5 MED, 1 LOW), 6 fixable | 0 | same |
| trivy image: `uv` binary | 17 (2 HIGH, 10 MED, 5 LOW), 17 fixable | 0 (not in the image) | same |
| trivy image: fixable HIGH/CRITICAL (the gate) | 2 | 0 | → `trivy-image-gate.txt` |
| trivy config (IaC + Dockerfile), nothing ignored | 2 MED, 2 LOW* | 0 MED, 2 LOW (both accepted: F-06, F-09) | → `trivy-config.json` |
| abuse and hostile-input tests | did not exist | 69 passed | `uv run pytest tests/test_security.py` |
| image size (bytes) | 394,508,773 | 231,665,207 | `docker image inspect --format '{{.Size}}'` |
| worst-case monthly bill under a distributed flood | $4,163 (2 instances, no gzip) | $930 (1 instance, gzip) | `python security/load/worst_case_cost.py security/load/results*.txt N` |

\* One baseline MEDIUM (GCP-0077, access logging) came from Trivy reading a stale, gitignored
`tfplan` file next to the HCL; plan files are now skipped.

**Gitleaks false positive.** After the Trivy reports were committed, Gitleaks flagged
`GPG_KEY=7169605F…6305` in them (4 hits). That is the public OpenPGP fingerprint of the
Python 3.12 release manager, set by the official `python` image to verify the Python
download. Nothing to rotate; `.gitleaks.toml` allows that exact value in `security/*/trivy-image.json`
only.

### Live check (deployed service, 2026-10-25)

A handful of requests against the owner's own deployment after the phase 5 deploy (the deploy
job's Trivy gate passed):

- `curl -sI $URL/`: CSP, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, HSTS present.
- `POST /recommend` with `http://169.254.169.254/entry/5` → 400 with the generic message.
- A 6,000-byte body → 413. A valid request → 200, response gzip-encoded.
- 140 parallel `GET /health` in 8 s, each with a different spoofed `X-Forwarded-For` → 120 × 200
  then 20 × 429: the limit keys on the address Google's front end adds, not on the spoofed one.

### Accepted risks

- **Debian base-image CVEs (F-03):** no fixed packages exist yet; the deploy gate blocks any
  fixable HIGH/CRITICAL, the weekly scan and Dependabot pick up fixes.
- **No CMEK, no bucket access logs (F-08, F-09):** public data; admin activity is audited.
- **Distributed flood cost (R-14):** reduced and measured, not capped. Per-IP limits do not
  stop many IPs; the budget alert only notifies.
- **In-memory rate limits:** per instance (now one instance), reset on restart.
- **`cloudscheduler.admin` and the human `Owner`:** scoped to Scheduler; Owner is needed to
  apply the foundation.

### What I would do next (not done)

- Cloud Armor (or another WAF) in front of Cloud Run for real rate limiting and bot blocking,
  or a budget-triggered function that sets ingress to internal: a hard cost cap.
- Generate an SBOM for each image (Trivy can write CycloneDX) and sign images with cosign,
  then only allow signed images on Cloud Run (Binary Authorization).
- Authentication if the service ever handles anything private.
- Track the scan counts over time from the weekly workflow artifacts.

## Findings

Status: **open** (not yet addressed), **fixed**, **accepted** (with reason), **n/a**.

### From tools

| ID | Tool | Severity | Description | Evidence | Status |
|---|---|---|---|---|---|
| F-01 | ruff S101 | low | `assert` in tests | `security/baseline/ruff-bandit.txt` (153 lines, all `tests/`) | n/a: pytest asserts are the test framework; ignored for `tests/**` only |
| F-02 | ruff S607 | low | `git` started by partial path (PATH lookup) | `fpl_advisor/evaluate.py:179` | fixed: absolute path from `shutil.which`; the remaining S603 is suppressed inline (fixed argument list, no shell, no user input) |
| F-03 | trivy image | 44 HIGH, 58 MED, 61 LOW, 1 UNK | Debian 13.7 base packages with known CVEs, none with a fixed version yet | `security/baseline/trivy-image.json` | accepted: 162 are "affected" and 2 "fix_deferred" in Debian, so no fixed package exists; 36 of the 44 HIGH are four util-linux CVEs counted across nine util-linux binary packages that the app never runs. The image is rebuilt on every deploy and Dependabot bumps the base digest, so fixes arrive when Debian ships them; the deploy gate blocks any fixable HIGH/CRITICAL |
| F-04 | trivy image | 5 MED, 1 LOW | `pip` 25.0.1 in the base image (not used at runtime) | same, Target `Python` | fixed: `pip` uninstalled from the runtime image (6 → 0, `security/phase3/`) |
| F-05 | trivy image | 2 HIGH, 10 MED, 5 LOW | `uv` 0.10.12 build tool shipped in the runtime image (Rust crates `rustls-webpki`, `quinn-proto`, `tar`, ...) | same, Target `usr/bin/uv` | fixed: multi-stage build, `uv` only in the build stage (17 → 0, `security/phase3/`); builder also bumped to uv 0.11.15 |
| F-06 | trivy config DS-0026 | low | No `HEALTHCHECK` in Dockerfile | `security/baseline/trivy-config.txt` | accepted (`.trivyignore`): Cloud Run ignores Docker `HEALTHCHECK`; it uses the startup probe on `/health` in `infra/app/main.tf` |
| F-07 | trivy config GCP-0078 | medium | Data bucket versioning disabled | `infra/bootstrap/main.tf:71` | fixed in code, **needs bootstrap apply**: versioning on; noncurrent versions deleted after 7 days or when 3 newer exist |
| F-08 | trivy config GCP-0077 | medium | Data bucket access logging not enabled | `infra/bootstrap/main.tf:71` | accepted (`.trivyignore`): access logs need a second bucket or paid Data Access audit logs; objects are public FPL data and a model, and admin activity is audited by default. (Trivy raised this only on a stale local `tfplan` file, not on the HCL; plan files are now skipped) |
| F-09 | trivy config GCP-0066 | low | Data bucket not encrypted with a customer-managed key (CMEK) | `infra/bootstrap/main.tf:71` | accepted (`.trivyignore`): Google-managed encryption at rest; a KMS key adds cost and key handling with no confidentiality gain for public data |

### From code and configuration review

| ID | Area | Severity (my rating) | Description | Evidence | Status |
|---|---|---|---|---|---|
| R-01 | Input | low | Team input accepts any URL containing `/entry/{n}` from any host (`http://169.254.169.254/entry/5` → 5) and non-ASCII digits (`"١٢٣"` → 123). Not SSRF: the input is never fetched | `fpl_advisor/api.py:77-79`; `python -c "from fpl_advisor.api import parse_team_id; print(parse_team_id('١٢٣'))"` | fixed: `api.parse_team` accepts only ASCII digits (1 to 99,999,999) or the exact `fantasy.premierleague.com/[xx/]entry/{id}[/event/{gw}]` URL, max 200 chars; 26 hostile inputs in `tests/test_security.py` |
| R-02 | Input | medium | No bounds on team ID or on the gameweek in a URL; a URL ending `/event/38` makes `forecast(39)` raise `KeyError` → HTTP 500 | `fpl_advisor/api.py:85-90`, `fpl_advisor/advisor.py:77`; `Advisor().forecast(39)` raises `KeyError 39` | fixed: gameweek bounded 1-38 in URL and body; `forecast` raises a clear 400 "Season is over" after GW38 (`test_season_over_is_a_clear_error`) |
| R-03 | Abuse | medium | No rate limit, body size limit or cap on concurrent optimizations on `POST /recommend` (several CPU-seconds each) | `fpl_advisor/app.py` (no middleware) | fixed: 10 req/min per IP on `/recommend`, 60 on `/predictions` (429 + Retry-After), 4 KiB body limit (413, also for streamed bodies), 2 concurrent optimizations per instance (503 when busy for 10 s), 60 s work timeout (504); `fpl_advisor/security.py`, tests |
| R-04 | Errors | low | 503 responses echo exception text (upstream URL paths, local model path) | `fpl_advisor/app.py:136,138` | fixed: 503/504 texts are fixed strings; unexpected errors return `{"detail": "Internal error"}` and the traceback goes to JSON logs only; logs carry the validated team ID, never the raw input |
| R-05 | Headers | low | No CSP, `X-Content-Type-Options`, `Referrer-Policy` or framing protection; page uses inline script | `fpl_advisor/static/index.html` | fixed: CSP `script-src 'self'; style-src 'self'` (no inline script or style; page split into `app.js`/`app.css`), `frame-ancestors 'none'`, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, HSTS on every response including 413/429. `/docs` keeps a looser CSP for Swagger UI |
| R-06 | Outbound | low | FPL client follows redirects (to any host) and reads responses of any size | `fpl_advisor/api.py:138` (`requests` default `allow_redirects=True`) | fixed: `allow_redirects=False`, (5 s, 20 s) timeouts, 20 MB body cap (Content-Length and streamed), API paths must match `[a-z0-9_-]+/…/`; same for the past-season download (100 MB cap) |
| R-07 | Container | low | Base images pinned by tag, not digest; single stage keeps `uv` in the runtime image | `Dockerfile:1,3` | fixed: both base images pinned by digest, multi-stage build, non-root UID 10001 with no shell, app files owned by root (not writable by the app), allow-list `.dockerignore`; image 394.5 MB → 231.7 MB. Read-only root filesystem: n/a on Cloud Run (no such setting; the app writes only to `/tmp`) |
| R-08 | CI | medium | GitHub Actions pinned by tag, not commit SHA; `ci.yml` has no `permissions:` block (default token rights); no Dependabot | `.github/workflows/ci.yml:11-23`, `deploy.yml:26-44` | fixed: every action pinned by commit SHA (tag in a comment), `permissions: contents: read` on all workflows (deploy adds only `id-token: write`), `persist-credentials: false`, step outputs passed through `env`, Dependabot for uv, Docker, Actions and Terraform; deploy now scans the image and refuses to push one with a fixable HIGH/CRITICAL |
| R-09 | Federation | low | Workload Identity condition and binding use the repository **name**, which can be re-registered if the account is renamed or deleted; the `main` branch condition is already in place | `infra/bootstrap/main.tf:151,160` | fixed in code, **needs bootstrap apply** (BLOCKERS.md): condition pins numeric `repository_id`, `repository_owner_id`, `refs/heads/main` and `ref_type == branch`; the deployer binding uses `attribute.repository_id`; a Terraform precondition fails the plan if the condition loses any of these |
| R-10 | IAM | high | The default Compute Engine service account has `roles/editor` on the project (Google grants this automatically). None of our workloads use it, but anything deployed without an explicit account would run as Editor | `gcloud projects get-iam-policy fpl-advisor-snf` | fixed in code, **needs bootstrap apply**: `google_project_iam_member_remove` drops `roles/editor` from the default compute account |
| R-11 | Storage | medium | Terraform state bucket has public access prevention `inherited`, not `enforced` (it is versioned and uses uniform access) | `gcloud storage buckets describe gs://fpl-advisor-snf-tfstate`; created by `Makefile:42` | fixed in `make bootstrap` (`--public-access-prevention`, lifecycle keeps 10 old state versions); **existing bucket needs one command** (BLOCKERS.md) |
| R-12 | IAM | low | Deployer has project-level `roles/run.admin` and `roles/cloudscheduler.admin` | `infra/bootstrap/main.tf:116` | fixed in code, **needs bootstrap apply**: `run.admin` replaced by `run.developer` + custom role with only `run.services.setIamPolicy` and `run.jobs.setIamPolicy` (the two IAM writes Terraform makes). `cloudscheduler.admin` stays: it only covers Scheduler jobs. The human `Owner` stays (needed to apply bootstrap) |
| R-13 | Storage | low | Data bucket has no lifecycle rule capping growth | `infra/bootstrap/main.tf:71` | fixed in code, **needs bootstrap apply**: lifecycle deletes snapshots older than 730 days and aborts incomplete uploads after 1 day |
| R-14 | Cost | medium | A distributed flood (many source IPs, so per-IP limits do not apply) can run up the bill; the 55 NOK budget only alerts. Measured worst case: $4,163/month with 2 instances and no compression | `security/load/results-before.txt`; `python security/load/worst_case_cost.py security/load/results-before.txt 2` | mitigated, rest accepted: `max_instance_count` 2 → 1, gzip (app.js 13,120 → 4,411 bytes per request), 120 req/min per IP on every route. Worst case now $930/month (`worst_case_cost.py security/load/results.txt 1`), mostly per-request fees; single-IP floods get 429s after 120 requests. A hard cap needs Cloud Armor or a budget-triggered shutdown (future work); the manual kill switch is in README "Security" |
