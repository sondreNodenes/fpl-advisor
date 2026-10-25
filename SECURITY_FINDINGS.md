# Security findings

Every finding comes from a tool report in `security/` or a code review step with a file and
line. Threats and their reasoning are in [THREAT_MODEL.md](THREAT_MODEL.md).

## How to reproduce

```bash
make security                                    # all scans; reports in security/latest/
STRICT=0 security/scan.sh security/baseline      # how the baseline was recorded (report-only)
```

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

## Findings

Status: **open** (not yet addressed), **fixed**, **accepted** (with reason), **n/a**.

### From tools

| ID | Tool | Severity | Description | Evidence | Status |
|---|---|---|---|---|---|
| F-01 | ruff S101 | low | `assert` in tests | `security/baseline/ruff-bandit.txt` (153 lines, all `tests/`) | n/a: pytest asserts are the test framework; ignored for `tests/**` only |
| F-02 | ruff S607 | low | `git` started by partial path (PATH lookup) | `fpl_advisor/evaluate.py:179` | fixed: absolute path from `shutil.which`; the remaining S603 is suppressed inline (fixed argument list, no shell, no user input) |
| F-03 | trivy image | 44 HIGH, 58 MED, 61 LOW, 1 UNK | Debian 13.7 base packages with known CVEs, none with a fixed version yet | `security/baseline/trivy-image.json` | open |
| F-04 | trivy image | 5 MED, 1 LOW | `pip` 25.0.1 in the base image (not used at runtime) | same, Target `Python` | open |
| F-05 | trivy image | 2 HIGH, 10 MED, 5 LOW | `uv` 0.10.12 build tool shipped in the runtime image (Rust crates `rustls-webpki`, `quinn-proto`, `tar`, ...) | same, Target `usr/bin/uv` | open |
| F-06 | trivy config DS-0026 | low | No `HEALTHCHECK` in Dockerfile | `security/baseline/trivy-config.txt` | open |
| F-07 | trivy config GCP-0078 | medium | Data bucket versioning disabled | `infra/bootstrap/main.tf:71` | open |
| F-08 | trivy config GCP-0077 | medium | Data bucket access logging not enabled | `infra/bootstrap/main.tf:71` | open |
| F-09 | trivy config GCP-0066 | low | Data bucket not encrypted with a customer-managed key (CMEK) | `infra/bootstrap/main.tf:71` | open |

### From code and configuration review

| ID | Area | Severity (my rating) | Description | Evidence | Status |
|---|---|---|---|---|---|
| R-01 | Input | low | Team input accepts any URL containing `/entry/{n}` from any host (`http://169.254.169.254/entry/5` → 5) and non-ASCII digits (`"١٢٣"` → 123). Not SSRF: the input is never fetched | `fpl_advisor/api.py:77-79`; `python -c "from fpl_advisor.api import parse_team_id; print(parse_team_id('١٢٣'))"` | fixed: `api.parse_team` accepts only ASCII digits (1 to 99,999,999) or the exact `fantasy.premierleague.com/[xx/]entry/{id}[/event/{gw}]` URL, max 200 chars; 26 hostile inputs in `tests/test_security.py` |
| R-02 | Input | medium | No bounds on team ID or on the gameweek in a URL; a URL ending `/event/38` makes `forecast(39)` raise `KeyError` → HTTP 500 | `fpl_advisor/api.py:85-90`, `fpl_advisor/advisor.py:77`; `Advisor().forecast(39)` raises `KeyError 39` | fixed: gameweek bounded 1-38 in URL and body; `forecast` raises a clear 400 "Season is over" after GW38 (`test_season_over_is_a_clear_error`) |
| R-03 | Abuse | medium | No rate limit, body size limit or cap on concurrent optimizations on `POST /recommend` (several CPU-seconds each) | `fpl_advisor/app.py` (no middleware) | fixed: 10 req/min per IP on `/recommend`, 60 on `/predictions` (429 + Retry-After), 4 KiB body limit (413, also for streamed bodies), 2 concurrent optimizations per instance (503 when busy for 10 s), 60 s work timeout (504); `fpl_advisor/security.py`, tests |
| R-04 | Errors | low | 503 responses echo exception text (upstream URL paths, local model path) | `fpl_advisor/app.py:136,138` | fixed: 503/504 texts are fixed strings; unexpected errors return `{"detail": "Internal error"}` and the traceback goes to JSON logs only; logs carry the validated team ID, never the raw input |
| R-05 | Headers | low | No CSP, `X-Content-Type-Options`, `Referrer-Policy` or framing protection; page uses inline script | `fpl_advisor/static/index.html` | fixed: CSP `script-src 'self'; style-src 'self'` (no inline script or style; page split into `app.js`/`app.css`), `frame-ancestors 'none'`, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, HSTS on every response including 413/429. `/docs` keeps a looser CSP for Swagger UI |
| R-06 | Outbound | low | FPL client follows redirects (to any host) and reads responses of any size | `fpl_advisor/api.py:138` (`requests` default `allow_redirects=True`) | fixed: `allow_redirects=False`, (5 s, 20 s) timeouts, 20 MB body cap (Content-Length and streamed), API paths must match `[a-z0-9_-]+/…/`; same for the past-season download (100 MB cap) |
| R-07 | Container | low | Base images pinned by tag, not digest; single stage keeps `uv` in the runtime image | `Dockerfile:1,3` | open |
| R-08 | CI | medium | GitHub Actions pinned by tag, not commit SHA; `ci.yml` has no `permissions:` block (default token rights); no Dependabot | `.github/workflows/ci.yml:11-23`, `deploy.yml:26-44` | open |
| R-09 | Federation | low | Workload Identity condition and binding use the repository **name**, which can be re-registered if the account is renamed or deleted; the `main` branch condition is already in place | `infra/bootstrap/main.tf:151,160` | open |
| R-10 | IAM | high | The default Compute Engine service account has `roles/editor` on the project (Google grants this automatically). None of our workloads use it, but anything deployed without an explicit account would run as Editor | `gcloud projects get-iam-policy fpl-advisor-snf` | open |
| R-11 | Storage | medium | Terraform state bucket has public access prevention `inherited`, not `enforced` (it is versioned and uses uniform access) | `gcloud storage buckets describe gs://fpl-advisor-snf-tfstate`; created by `Makefile:42` | open |
| R-12 | IAM | low | Deployer has project-level `roles/run.admin` and `roles/cloudscheduler.admin` | `infra/bootstrap/main.tf:116` | open |
| R-13 | Storage | low | Data bucket has no lifecycle rule capping growth | `infra/bootstrap/main.tf:71` | open |
