#!/usr/bin/env python3
"""Light concurrency smoke / load probe for deployed or local backend.

  BASE_URL=http://127.0.0.1:8000 CONCURRENCY=30 DURATION_SEC=10 python scripts/load_smoke.py

Reports status counts and rough req/s. Does not authenticate — use only on open /health.
"""

from __future__ import annotations

import asyncio
import os
import time

import httpx

BASE_URL = (os.environ.get("BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
PATH = os.environ.get("LOAD_PATH", "/health")
CONCURRENCY = max(1, int(os.environ.get("CONCURRENCY") or "20"))
DURATION_SEC = max(1, float(os.environ.get("DURATION_SEC") or "5"))
TIMEOUT = float(os.environ.get("HTTP_TIMEOUT") or "10.0")


async def _one(client: httpx.AsyncClient, counts: dict[int, int]) -> None:
    try:
        r = await client.get(f"{BASE_URL}{PATH}", timeout=TIMEOUT)
        counts[r.status_code] = counts.get(r.status_code, 0) + 1
    except Exception:
        counts[-1] = counts.get(-1, 0) + 1


async def _worker(stop: asyncio.Event, client: httpx.AsyncClient, counts: dict[int, int]) -> None:
    while not stop.is_set():
        await _one(client, counts)


async def main() -> None:
    counts: dict[int, int] = {}
    stop = asyncio.Event()
    async with httpx.AsyncClient() as client:
        tasks = [
            asyncio.create_task(_worker(stop, client, counts)) for _ in range(CONCURRENCY)
        ]
        await asyncio.sleep(DURATION_SEC)
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    total = sum(counts.values())
    elapsed = DURATION_SEC
    rps = total / elapsed if elapsed else 0.0
    print(f"BASE_URL={BASE_URL} PATH={PATH} concurrency={CONCURRENCY} duration={DURATION_SEC}s")
    print(f"requests={total} ~{rps:.1f} req/s  status_counts={dict(sorted(counts.items()))}")


if __name__ == "__main__":
    asyncio.run(main())
