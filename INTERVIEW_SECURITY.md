# Interview questions: FPL Advisor security pass

Short first-person answers. Every fact comes from this repo (`THREAT_MODEL.md`,
`SECURITY_FINDINGS.md`, `security/`).

**1. Walk me through what you did.**
I started with a threat model: assets, trust boundaries and 14 STRIDE threats. Then I recorded
a baseline with five scanners (Gitleaks, pip-audit, ruff's bandit rules, Trivy for the image
and for Terraform) before touching anything. Then I hardened four layers: the app (input
validation, rate and size limits, safe errors, CSP), the container and CI (multi-stage image
pinned by digest, actions pinned by SHA, least-privilege workflow tokens, Dependabot), and the
cloud (IAM, buckets, Workload Identity, instance cap). Finally I re-ran everything and wrote
the before/after counts. `make security` reproduces them, and CI runs the same script on every
push and weekly.

**2. What was the most serious finding?**
Two, and neither came from a scanner. First, Google had given the default Compute Engine
service account the Editor role on my project. None of my workloads used it, but anything
deployed later without an explicit service account would have run as project Editor. I found
it by reading the live IAM policy, and the fix removes the grant in Terraform. Second, cost:
I measured that a distributed flood could run the bill to about $4,163 a month with the
original settings, and a budget alert only sends an email.

**3. What did the scanners miss?**
Everything that needs context. They missed the Editor grant, because it lives in the live
project, not in my code. They missed that the Workload Identity condition used the
repository name, which can be re-registered, instead of the numeric ID. They missed the cost
exposure, a crash where a team URL ending in `/event/38` returned HTTP 500, that the input
parser accepted Arabic-Indic digits, and that the HTTP client followed redirects. They also
produced noise: 153 of 154 bandit findings were asserts in tests, and Gitleaks flagged the
public Python release-signing fingerprint as a secret.

**4. How would you attack this system?**
The cheapest attack is on the wallet: flood it from many IPs so per-IP limits do not apply. I
measured that at about $930 a month worst case now, down from $4,163. I would also try SSRF
through the team URL, for example the metadata server at 169.254.169.254, but the input is
never fetched and only the exact FPL URL pattern is accepted. I would look for XSS through
player names from the FPL API, which are escaped and blocked by a CSP with no inline script.
For the pipeline, I would try to get a deploy token from a fork's pull request, but the
federation condition requires this repository's numeric ID and the main branch. The realistic
remaining paths are a compromised dependency or a stolen GitHub account with push rights.

**5. How does Workload Identity Federation work, and what does your condition protect against?**
GitHub signs a short-lived OIDC token describing the workflow run: repository, owner, branch,
event. Google verifies it and evaluates my attribute condition. Only if that passes does it
issue a short-lived token for the deploy service account, so there is no JSON key to leak. My
condition requires the numeric repository ID, the numeric owner ID, `refs/heads/main` and a
branch ref. That blocks other repositories, forks, pull requests and feature branches, and a
renamed or re-registered repository name. A Terraform precondition fails the plan if someone
removes any of those checks.

**6. How did you decide what to fix and what to accept?**
By reachability and available fixes, not raw counts. Of 187 image vulnerabilities, 23 had
fixes, and all of them were in pip and uv, which the app never runs, so I removed those tools
from the runtime image. The 164 Debian ones have no fixed package yet, so I accepted them. The
deploy job blocks any fixable HIGH or CRITICAL instead. I accepted customer-managed encryption
keys and bucket access logs because the data is public FPL statistics. Each accepted risk has
a written reason.

**7. How do you rate limit without Redis or Cloud Armor?**
In memory, per instance: a sliding window per client IP, 120 requests a minute on every route
and 10 on the expensive `/recommend`. I capped the service at one instance, so the limit is
exact. The client IP is the last X-Forwarded-For entry, which Google's front end appends;
earlier entries are client-controlled and ignored. There is also a 4 KiB body limit, and at
most two optimizations run at once per instance, with 503 when busy and 504 after 60 seconds.
The weakness is that it does not stop many IPs and resets on restart; that is in the accepted
risks.

**8. How did you prove the hardening works?**
With tests and re-runs, not claims. `tests/test_security.py` has 69 tests:
- 26 hostile inputs (metadata IP, look-alike hosts, `file://`, path traversal, Unicode digits,
  5,000-character strings)
- rate limit and Retry-After
- oversized bodies, including streamed ones
- busy and timeout responses
- no stack traces or internal paths in errors
- the security headers

I also checked in a headless browser that the page still renders under the strict CSP.
`make security` runs the scanners and these tests together.

**9. What did you change in the container, and what did it buy you?**
A two-stage build with both base images pinned by digest. Only the virtualenv reaches the
final image. pip is removed, it runs as UID 10001 with no shell, the app files are owned by
root so the app cannot modify itself, and an allow-list `.dockerignore` keeps `.git`, `.env`
and data out. Fixable vulnerabilities went from 23 to 0, and the image went from 394.5 MB to
231.7 MB.

**10. What would you do next?**
A hard cost cap first. Either Cloud Armor in front of Cloud Run, or a budget alert that
triggers a small function which sets the service's ingress to internal. Then supply chain
provenance: an SBOM per image and cosign signatures, with Binary Authorization so only signed
images can run. Authentication only if the service ever stores anything private. I would also
finish applying the foundation changes, which need my owner rights, and re-run the deploy to
prove the narrower deploy role still works.
