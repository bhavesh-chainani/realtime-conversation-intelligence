"""Benchmark suggestion latency on a demo script.

Replays each Customer line of a scenario (with the customer context known at that
point) through the suggestion pipeline and prints p50/p95 latency plus which case
IDs each beat linked.

Usage:
  realtime-venv/bin/python scripts/bench_llm.py [--scenario sarah_lim_brightpath]
      [--pipeline single|router] [--runs 2] [--effort none|minimal|low|default]
      [--model openai.gpt-5.4-mini]
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config as cfg  # noqa: E402
from backend.demo_cache import (  # noqa: E402
    known_context_by_line,
    load_scenario,
    transcript_upto,
)
from backend.suggestions_core import compute_suggestions  # noqa: E402


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="sarah_lim_brightpath")
    parser.add_argument("--pipeline", default="single", choices=["single", "router"])
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--effort", default=None, help="none|minimal|low|default")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    if args.effort is not None:
        cfg.LLM_REASONING_EFFORT = "" if args.effort == "default" else args.effort
    if args.model:
        cfg.SUGGESTION_MODEL = args.model

    scenario = load_scenario(args.scenario)
    lines = scenario["lines"]
    context = known_context_by_line(lines)
    print(
        f"pipeline={args.pipeline} model={cfg.SUGGESTION_MODEL} "
        f"effort={cfg.LLM_REASONING_EFFORT or 'default'} runs={args.runs}"
    )

    totals: list[float] = []
    llms: list[float] = []
    failures = 0
    for run in range(args.runs):
        for index, line in enumerate(lines):
            if line["role"] != "customer":
                continue
            profile, cases = context[line["id"]]
            started = time.perf_counter()
            body = await compute_suggestions(
                transcript_upto(lines, index),
                max_suggestions=cfg.SUGGESTION_MAX,
                customer_profile=profile,
                customer_cases=cases,
                pipeline=args.pipeline,
            )
            total_ms = (time.perf_counter() - started) * 1000
            if body.get("fallback"):
                failures += 1
                print(f"  run{run} {line['id']}: FAILED {body.get('error')}")
                continue
            totals.append(total_ms)
            llms.append(float(body.get("timings", {}).get("llm_ms") or total_ms))
            linked = sorted({cid for s in body["suggestions"] for cid in s.get("linked_records", [])})
            expected = sorted((line.get("beat") or {}).get("expect", {}).get("linked_records", []))
            mark = "" if not expected else ("  OK" if set(expected) <= set(linked) else f"  expected {expected}")
            print(f"  run{run} {line['id']}: {total_ms:6.0f}ms linked={linked}{mark}")
            if run == 0 and body["suggestions"]:
                first = body["suggestions"][0]
                print(f"         -> {first['details'].get('possibleConversation', '')[:140]}")

    if totals:
        print(
            f"\ntotal  p50={statistics.median(totals):.0f}ms p95={pct(totals, 0.95):.0f}ms "
            f"max={max(totals):.0f}ms | llm p50={statistics.median(llms):.0f}ms | failures={failures}"
        )


if __name__ == "__main__":
    asyncio.run(main())
