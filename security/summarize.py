"""Count findings per tool and severity from the raw reports in a scan directory.

Usage: ``python security/summarize.py security/latest`` (writes summary.json there too).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]


def _load(path: Path):
    return json.loads(path.read_text()) if path.exists() and path.stat().st_size else None


def summarize(out: Path) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    leaks = _load(out / "gitleaks.json")
    if leaks is not None:
        summary["gitleaks (secrets)"] = {"findings": len(leaks)}
    for group in ("runtime", "dev"):
        audit = _load(out / f"pip-audit-{group}.json")
        if audit is not None:
            vulns = sum(len(d.get("vulns", [])) for d in audit.get("dependencies", []))
            summary[f"pip-audit ({group})"] = {"vulnerabilities": vulns}
    ruff = out / "ruff-bandit.txt"
    if ruff.exists():
        rules = Counter(line.split()[1] for line in ruff.read_text().splitlines() if ": S" in line)
        summary["ruff S (bandit)"] = dict(sorted(rules.items())) or {"findings": 0}
    image = _load(out / "trivy-image.json")
    if image is not None:
        for result in image.get("Results", []):
            vulns = result.get("Vulnerabilities") or []
            counts = Counter(v["Severity"] for v in vulns)
            fixable = sum(1 for v in vulns if v.get("FixedVersion"))
            key = f"trivy image: {result.get('Type')} ({result['Target']})"
            summary[key] = {s: counts[s] for s in SEVERITIES if counts[s]} | {
                "total": len(vulns),
                "fixable": fixable,
            }
    config = _load(out / "trivy-config.json")
    if config is not None:
        seen: set[tuple] = set()
        counts = Counter()
        for result in config.get("Results", []):
            for m in result.get("Misconfigurations") or []:
                # Trivy reports a Terraform root twice (directory and module); count once.
                key = (m["ID"], m.get("CauseMetadata", {}).get("Resource"))
                if key not in seen:
                    seen.add(key)
                    counts[m["Severity"]] += 1
        summary["trivy config (IaC, Dockerfile)"] = {
            s: counts[s] for s in SEVERITIES if counts[s]
        } or {"findings": 0}
    return summary


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "security/latest")
    summary = summarize(out)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    for tool, counts in summary.items():
        print(f"{tool:<55} " + ", ".join(f"{k} {v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
