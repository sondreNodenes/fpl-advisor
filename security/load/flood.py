"""Measure how many requests per second one app instance serves under a flood.

Each request carries a random X-Forwarded-For address, so per-IP rate limits do not apply:
this is the distributed-attack case. Run it against a local container with Cloud Run's size:

    docker run -d --name fplt --cpus=1 --memory=1g -p 8766:8080 \\
      -e FPL_TRUST_PROXY_HEADERS=1 ... fpl-advisor:scan
    uv run python security/load/flood.py http://localhost:8766/static/app.js 15 20

Only localhost targets are accepted: this must never be pointed at someone else's system
(or at the deployed service, which would bill the owner).
"""

from __future__ import annotations

import multiprocessing as mp
import random
import sys
import time
from urllib.parse import urlparse

import requests


def worker(url: str, seconds: float, seed: int, queue: mp.Queue) -> None:
    session = requests.Session()
    rnd = random.Random(seed)  # noqa: S311 - fake client IPs, not security-sensitive
    count, codes, wire = 0, {}, 0
    end = time.time() + seconds
    while time.time() < end:
        ip = f"10.{rnd.randrange(256)}.{rnd.randrange(256)}.{rnd.randrange(256)}"
        response = session.get(url, headers={"X-Forwarded-For": ip}, timeout=10)
        count += 1
        codes[response.status_code] = codes.get(response.status_code, 0) + 1
        wire += int(response.headers.get("content-length", len(response.content)))
    queue.put((count, codes, wire))


def main() -> None:
    url, seconds, procs = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
    if urlparse(url).hostname not in {"localhost", "127.0.0.1"}:
        sys.exit("Refusing: flood.py only targets localhost")
    queue: mp.Queue = mp.Queue()
    workers = [mp.Process(target=worker, args=(url, seconds, i, queue)) for i in range(procs)]
    for w in workers:
        w.start()
    results = [queue.get() for _ in workers]
    for w in workers:
        w.join()
    total = sum(r[0] for r in results)
    codes: dict[int, int] = {}
    for _, c, _ in results:
        for k, v in c.items():
            codes[k] = codes.get(k, 0) + v
    wire = sum(r[2] for r in results)
    rate, size = total / seconds, wire / total
    print(f"url={url} rps={rate:.0f} wire_bytes_per_request={size:.0f} codes={codes}")


if __name__ == "__main__":
    main()
