# CV bullets: security pass

Every number has the command that produces it.

- Built a security pipeline for a public Cloud Run service that runs on every push and weekly:
  secret scanning across full git history, dependency CVE audit, static analysis, and container
  image and Terraform scans, plus 69 abuse tests. Cut fixable container vulnerabilities from 23
  to 0 and image size from 395 MB to 232 MB.
  - Scanners and gates: `make security`, `.github/workflows/security.yml`
  - Fixable vulnerabilities, 23 → 0: `fixable` in `security/baseline/summary.json` and `security/after/summary.json`
  - 69 abuse tests: `uv run pytest tests/test_security.py`
  - Image size: `security/*/image-size-bytes.txt`
- Threat-modelled and hardened the app and its GCP project:
  - strict input validation against SSRF and injection (26 hostile-input tests)
  - per-IP rate limits, body and concurrency limits, and a strict Content-Security-Policy
  - Workload Identity Federation pinned to immutable GitHub repository and owner IDs
  - least-privilege service accounts, including removing a default Editor grant

  A measured denial-of-wallet test cut the worst-case flood cost from $4,163 to $930 a month.
  - Flood cost: `python security/load/worst_case_cost.py security/load/results-before.txt 2` and `... results.txt 1`
  - Threats: `THREAT_MODEL.md`
  - Fixes and accepted risks: `SECURITY_FINDINGS.md`

**Application text box (2 sentences):**
I threat-modelled and hardened my public Fantasy Premier League app on Google Cloud. I added
secret, dependency, code, container and Terraform scanning to CI, along with 69 abuse tests,
which cut fixable container vulnerabilities from 23 to 0. I also applied least-privilege IAM,
keyless GitHub-to-GCP deployment pinned to immutable repository IDs, and rate limiting that
reduced the measured worst-case flood cost from $4,163 to $930 a month.
