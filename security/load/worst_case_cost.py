"""Worst-case monthly Cloud Run bill if the public service is flooded all month.

Inputs are measured, not assumed: requests/second and bytes/request per route come from
``security/load/flood.py`` against a 1 vCPU / 1 GiB container (``results.txt``).
Prices are the europe-north1 Cloud Billing Catalog SKUs (service 152E-C115-5142), queried
2026-10-25 with:
    curl -H "Authorization: Bearer $(gcloud auth print-access-token)" \\
      "https://cloudbilling.googleapis.com/v1/services/152E-C115-5142/skus?currencyCode=USD"
The free tier is ignored for CPU and memory (worst case), and counted for requests (the SKU
itself starts billing at 2 million).

Usage: python security/load/worst_case_cost.py [results.txt] [max_instances]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SECONDS_PER_MONTH = 30 * 24 * 3600
CPU_PER_VCPU_SECOND = 0.000024  # SKU 4856-B847-F1EB, request-based billing
MEMORY_PER_GIB_SECOND = 0.0000025  # SKU 02A2-9231-36A6, request-based billing
REQUESTS_FREE, PER_REQUEST = 2_000_000, 0.40 / 1_000_000  # "Requests" SKU, global
# Internet egress Europe to Europe: (from GiB, price per GiB)
EGRESS_TIERS = [(0, 0.105), (10_240, 0.08), (153_600, 0.06)]
VCPU, MEMORY_GIB = 1, 1  # infra/app/main.tf


def egress_cost(gib: float) -> float:
    cost = 0.0
    for i, (start, price) in enumerate(EGRESS_TIERS):
        end = EGRESS_TIERS[i + 1][0] if i + 1 < len(EGRESS_TIERS) else float("inf")
        if gib > start:
            cost += (min(gib, end) - start) * price
    return cost


def route_cost(rps: float, bytes_per_request: float, instances: int) -> dict[str, float]:
    requests = rps * instances * SECONDS_PER_MONTH
    gib = requests * bytes_per_request / 2**30
    return {
        "instances": instances
        * SECONDS_PER_MONTH
        * (VCPU * CPU_PER_VCPU_SECOND + MEMORY_GIB * MEMORY_PER_GIB_SECOND),
        "requests": max(0.0, requests - REQUESTS_FREE) * PER_REQUEST,
        "egress": egress_cost(gib),
    }


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "security/load/results.txt")
    instances = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    worst = 0.0
    for line in path.read_text().splitlines():
        m = re.search(r"url=(\S+) rps=(\d+) wire_bytes_per_request=(\d+)", line)
        if not m:
            continue
        costs = route_cost(float(m[2]), float(m[3]), instances)
        total = sum(costs.values())
        worst = max(worst, total)
        parts = ", ".join(f"{k} ${v:,.0f}" for k, v in costs.items())
        print(f"{m[1]:<40} {m[2]:>5} rps x {instances}: {parts} = ${total:,.0f}/month")
    print(f"Worst case with max_instances={instances}: ${worst:,.0f}/month")


if __name__ == "__main__":
    main()
