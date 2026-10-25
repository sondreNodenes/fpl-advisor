# Threat model

Written before any security change (2026-10-25, against commit `2d0c06b`), so it describes
the system as it was. The last column says which phase of the security pass addresses each
threat.

## What the system does

A public web page and JSON API (`POST /recommend`, `GET /predictions`, `GET /health`) on
Cloud Run. A visitor types an FPL team ID or URL; the service fetches that team's public data
from the FPL API, predicts points with a model loaded from a Cloud Storage bucket, runs a MILP
optimizer (several seconds of CPU per request) and returns advice. A Cloud Run job, started by
Cloud Scheduler, downloads new gameweek data from the FPL API and retrains the model into the
same bucket. GitHub Actions builds the image and applies Terraform for the app; the
foundation (IAM, Workload Identity Federation, registry, buckets) is applied by the owner by
hand. There are no user accounts, no database and no secrets at runtime.

## Assets

| Asset | Why it matters |
|---|---|
| GCP project `fpl-advisor-snf` | Control of it means control of everything below |
| Cloud bill (billing account, 55 NOK/month budget alert) | Abuse turns into real money; the budget only alerts, it does not cap |
| Data bucket `*-fpl-data` (snapshots, model) | Tampering changes every recommendation; loss is recoverable by re-running the job |
| Terraform state bucket `*-tfstate` | Holds resource IDs and IAM; write access is equivalent to deploy access |
| Service accounts `fpl-service`, `fpl-job`, `fpl-scheduler`, `fpl-deployer` | Their roles are what an attacker inherits after a compromise |
| Artifact Registry images | A poisoned image runs with the service account's rights |
| GitHub repo and Actions | Pushes to `main` deploy to production through Workload Identity Federation |
| Uptime of the public service | The only user-facing promise |

## Trust boundaries

1. **Internet → Cloud Run service.** Anyone can call it (`allUsers` has `run.invoker`). All
   request data (team string, gameweek, query parameters, headers) is untrusted.
2. **Cloud Run → FPL API.** A third party over HTTPS. Its responses are untrusted input
   (size, shape, redirects, maintenance pages).
3. **Cloud Run → Cloud Storage.** The service reads the bucket through a read-only volume;
   the job writes through a read-write volume. Identity: the service accounts.
4. **GitHub Actions → GCP.** An OIDC token from GitHub is exchanged for a short-lived token
   for `fpl-deployer` via Workload Identity Federation. No stored keys.
5. **Developer machine → GCP.** The owner applies `infra/bootstrap` with owner rights.
6. **Build → dependencies.** PyPI packages (locked in `uv.lock`), base images from Docker
   Hub and GHCR, and third-party GitHub Actions run inside the build.

## Threats (STRIDE)

Likelihood and impact are low / med / high for this project: a hobby service with no user
data, so impact is mostly cost and integrity of the advice.

| # | STRIDE | Threat | Likelihood | Impact | Current mitigation | Planned mitigation (phase) |
|---|---|---|---|---|---|---|
| T1 | DoS | Abuse of `POST /recommend`: each call costs seconds of CPU and up to 5 FPL API calls; a script can keep both instances busy and push the bill up | high | med | `max_instance_count = 2`, concurrency 10, timeout 120 s, FPL responses cached 15 min | Per-client rate limit, cap on concurrent optimizations, body size limit, request timeout, clear 429/413/503 (2); document worst-case cost (4) |
| T2 | Spoofing / Tampering | SSRF through the team input: a URL such as `http://169.254.169.254/...` being fetched | low | high | `parse_team_id` extracts digits after `/entry/` and never fetches the input; base URL is a constant | Accept only an integer or the exact `fantasy.premierleague.com/.../entry/{id}` pattern, bound ID and gameweek, tests with hostile inputs (2) |
| T3 | Tampering | Injection through team ID or gameweek into the FPL path or cache file name (path traversal) | low | med | Only `int` values reach paths | Strict parsing with ASCII digits and bounds; tests (2) |
| T4 | DoS | Malicious or broken upstream: FPL API (or a redirect) returns a huge body or redirects elsewhere | low | med | 20 s timeout, retries, non-JSON check | No redirects to other hosts, response size limit, allow-listed host (2) |
| T5 | Info disclosure | Error messages or logs reveal internals (paths, stack traces) or echo raw user input | med | low | FastAPI hides stack traces on 500s; app does not log inputs | Generic error texts, structured logs with only the validated ID (2) |
| T6 | Tampering (XSS) | Player names or news from the FPL API rendered into the page as HTML | low | med | `esc()` on strings in the page | Content-Security-Policy with no inline script, other security headers (2) |
| T7 | Tampering | Supply chain: a vulnerable or malicious PyPI package, base image or GitHub Action | med | high | `uv.lock` pins versions and hashes; actions pinned by tag only | `pip-audit`, `trivy image` in CI (1); actions pinned by SHA, base images by digest, Dependabot (3) |
| T8 | Elevation of privilege | Over-privileged service accounts: a compromised container or workflow gets more than it needs | med | high | One account per workload, bucket-level roles, deployer without IAM admin | Review every binding, remove what is unused, document why the rest stay (4) |
| T9 | Spoofing | Workload Identity Federation misuse: another repo, a fork, a pull request or another branch gets a token for `fpl-deployer` | low | high | Provider condition: repository name and `refs/heads/main` | Pin to immutable repository and owner IDs (names can be re-registered) and verify in Terraform (3) |
| T10 | Info disclosure | Secrets leak into the repo, image or logs | low | high | No runtime secrets; `.gitignore` for state files; `.dockerignore` | `gitleaks` over full history in CI (1); confirm `.env`/`.git` never reach the image (3) |
| T11 | DoS (financial) | Bill run-up from traffic, a runaway job or storage growth | med | med | Scale to zero, max 2 instances, 55 NOK budget alert, registry cleanup policy | Lower ceilings, bucket lifecycle rules, worst-case cost written down (4) |
| T12 | Tampering | State bucket or data bucket made public or overwritten | low | high | Uniform access, public access prevention on the data bucket, versioning on state | Same controls on the state bucket, versioning plus lifecycle on data (4) |
| T13 | Repudiation | No record of who deployed what | low | low | GitHub Actions logs, Cloud Audit Logs (admin activity, on by default) | Accepted: admin activity logs are enough for a one-person project |
| T14 | Elevation of privilege | Script injection in workflows through untrusted event data | low | high | Workflows use only `github.sha` and our own step outputs | Least-privilege `permissions:` everywhere, values passed through `env` (3) |

## Out of scope

Authentication and accounts, a WAF or Cloud Armor, penetration testing of anything outside
this repo and project, compliance frameworks, paid services. The MCP server runs locally over
stdio on the owner's machine, so it has no network exposure.
