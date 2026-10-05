"""Simulate whole calls against a running backend to check the assistant end to end (dev tool, not used
by the app).

An LLM plays the caller from a short brief; "Staff" says each suggestion word for word. Every customer
turn goes through POST /assist like the browser does (caller card, lookup, suggestion), then the call is
wrapped up (POST /wrapup), optionally saved (POST /cases) and followed by a second call from the same
caller. Prints the transcript and a checklist.

Usage:
  python scripts/simulate_call.py --story salary --save        # against http://localhost:8000
  python scripts/simulate_call.py --story all --backend http://localhost:8000
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.llm import get_async_llm_client, get_suggestion_model  # noqa: E402
from backend.profile import Profile  # noqa: E402

OPENER = "Hello, Employment Advice Centre, how can I help you today?"

STORIES = {
    "salary": {
        "title": "New caller, unpaid salary -> TADM claim -> follow-up call",
        "facts": (
            "Your name is Ahmad Rahim. Phone 8111 2222, email ahmad.rahim@gmail.com. You have never "
            "called the centre before. You are a kitchen assistant at Lucky Star F&B Pte Ltd. Your salary "
            "for August and September (SGD 1,900 a month) has not been paid. You still work there. You "
            "asked your boss on WhatsApp twice and he keeps saying 'next week'. You have payslips for "
            "earlier months and the WhatsApp messages, but no copy of your contract."
        ),
        "opening": "Hi, my boss hasn't paid my salary for two months and I don't know what to do.",
        "follow_up_facts": (
            "You are Ahmad Rahim (phone 8111 2222) calling back a week later. You filed the TADM salary "
            "claim online yesterday and got a reference number. You want to know what happens next."
        ),
        "follow_up_opening": "Hi, I called last week about my unpaid salary. I've filed the claim now.",
    },
    "rajesh": {
        "title": "Returning caller with an overdue document, new working-hours issue",
        "facts": (
            "Your name is Rajesh Kumar. Phone 8234 5678, email rajesh.kumar@gmail.com. You work for "
            "Harbourline Construction Pte Ltd. Your manager keeps rostering you on a closing shift until "
            "11pm then an opening shift at 7am, six days a week, and often cancels your rest day. Your "
            "basic salary is SGD 1,600. Overtime is only partly paid. You have photos of the rosters. "
            "About your work permit renewal: you did the medical check-up last week, have the report, and "
            "can send it today."
        ),
        "opening": (
            "Hi, my manager keeps scheduling me for back-to-back closing and opening shifts. I'm exhausted "
            "and I'm not sure if that's allowed."
        ),
    },
    "katherine": {
        "title": "Repeat employer, possible retaliation linked to an open case",
        "facts": (
            "Your name is Katherine Liao. Phone 9123 4567, email katherine.liao@gmail.com. You work at "
            "Brightpath Logistics Pte Ltd. Last month you complained to HR that six days of your annual "
            "leave were forfeited. This month your salary was cut by SGD 300 with no explanation. You "
            "still work there. You have this month's payslip and your email to HR. HR has not replied."
        ),
        "opening": "Hi, I'm calling because my salary was cut this month and nobody told me why.",
    },
}

CALLER_RULES = """You are role-playing a caller phoning an employment advice centre in Singapore.
Stay in character. Your facts:
{facts}

Rules:
- Reply as the caller in 1-2 short spoken sentences, like a real phone call.
- Answer what Staff just asked. Do not volunteer all your facts at once.
- If asked something your facts do not cover, say you are not sure.
- Give your phone number as digits (e.g. 8111 2222) and your email as written.
- Do not end the call until Staff has told you what to do next AND when the centre will follow up.
  If Staff has given you advice but not said when they will follow up, ask once. Once both are
  clear, thank them, say goodbye and end that message with [END].
Reply with the caller's words only."""


async def caller_says(facts: str, transcript: list[tuple[str, str]]) -> str:
    client = get_async_llm_client()
    convo = "\n".join(f"{role}: {text}" for role, text in transcript)
    response = await client.chat.completions.create(
        model=get_suggestion_model(),
        temperature=0.4,
        messages=[
            {"role": "system", "content": CALLER_RULES.format(facts=facts)},
            {
                "role": "user",
                "content": f"The call so far:\n{convo}\n\nWhat do you say next?",
            },
        ],
    )
    return (response.choices[0].message.content or "").strip().strip('"')


class Call:
    """The browser's side of one call: caller card, last lookup and the suggestion on screen."""

    def __init__(self, http: httpx.AsyncClient, backend: str):
        self.http, self.backend = http, backend
        self.turns: list[dict[str, str]] = []
        self.profile = Profile()
        self.history: dict = {
            "lookup_key": None,
            "match_strategy": None,
            "cases": [],
            "status": None,
        }
        self.suggestion: dict | None = None
        self.notes: list[str] = []
        self.llm_ms: list[float] = []

    def say(self, role: str, text: str) -> None:
        self.turns.append({"role": role, "text": text})

    def transcript(self) -> list[tuple[str, str]]:
        return [("Staff" if t["role"] == "staff" else "Customer", t["text"]) for t in self.turns]

    async def assist(self) -> dict | None:
        body = {
            "turns": self.turns,
            "customer": {k: v for k, v in self.profile.values.items() if v},
            "sources": self.profile.sources,
            "history": self.history,
            "previous_suggestions": (
                [self.suggestion["details"]["possibleConversation"]] if self.suggestion else []
            ),
            "max_suggestions": 1,
        }
        async with self.http.stream("POST", f"{self.backend}/assist", json=body) as r:
            async for line in r.aiter_lines():
                if not line:
                    continue
                e = json.loads(line)
                if e["type"] == "customer":
                    if accepted := self.profile.apply(e["patch"], e["source"]):
                        self.notes.append(f"card({e['source']}) {accepted}")
                elif e["type"] == "history" and e["status"] != "loading":
                    self.history = {
                        "lookup_key": e["lookup_key"],
                        "match_strategy": e["match_strategy"],
                        "cases": e["cases"],
                        "status": e["status"],
                    }
                    ids = [c["case_id"] for c in e["cases"]]
                    self.notes.append(f"history {e['status']} {e['match_strategy'] or ''} {ids}")
                elif e["type"] == "suggestions" and e["suggestions"]:
                    self.suggestion = e["suggestions"][0]
                    if e["timings"].get("llm_ms"):
                        self.llm_ms.append(e["timings"]["llm_ms"])
                elif e["type"] == "error":
                    self.notes.append(f"ERROR {e['stage']}: {e['message']}")
        return self.suggestion


async def run_call(http, backend, facts, opening, max_rounds, label) -> Call:
    call = Call(http, backend)
    call.say("staff", OPENER)
    caller_line = opening
    print(f"\n--- {label} ---\nStaff:    {OPENER}")
    for _ in range(max_rounds):
        ended = "[END]" in caller_line
        call.say("customer", caller_line.replace("[END]", "").strip())
        print(f"Customer: {call.turns[-1]['text']}")
        before = call.suggestion
        suggestion = await call.assist()
        for note in call.notes:
            print(f"          · {note}")
        call.notes.clear()
        if not suggestion or suggestion is before:  # the agent chose not to suggest
            print("          · (no new suggestion)")
            break
        line = suggestion["details"]["possibleConversation"]
        cites = (
            f"  [cites {', '.join(suggestion['linked_records'])}]" if suggestion.get("linked_records") else ""
        )
        print(f"Staff:    {line}   <{suggestion.get('type')}>{cites}")
        call.say("staff", line)
        if ended:
            print("          · (caller hung up)")
            break
        caller_line = await caller_says(facts, call.transcript())
    return call


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'x' if ok else ' '}] {name}{f' ({detail})' if detail else ''}")
    return ok


def staff_lines(call: Call) -> list[str]:
    return [t["text"] for t in call.turns if t["role"] == "staff"][1:]


async def run_story(key: str, backend: str, save: bool, max_rounds: int) -> bool:
    story = STORIES[key]
    print(f"\n================ {key}: {story['title']} ================")
    async with httpx.AsyncClient(timeout=90) as http:
        call = await run_call(http, backend, story["facts"], story["opening"], max_rounds, "call")
        lines = staff_lines(call)
        started = time.perf_counter()
        wrap = (
            await http.post(
                f"{backend}/wrapup",
                json={
                    "turns": call.turns,
                    "customer": {k: v for k, v in call.profile.values.items() if v},
                    "history": {
                        "match_strategy": call.history["match_strategy"],
                        "cases": call.history["cases"],
                    },
                },
            )
        ).json()
        wrap_s = time.perf_counter() - started
        w = wrap.get("wrapup") or {}
        print(f"\n--- wrap-up ({wrap_s:.1f}s) ---")
        print(json.dumps(w, indent=2))

        saved = {}
        follow = None
        if save and w:
            saved = (
                await http.post(
                    f"{backend}/cases",
                    json={
                        "customer": {k: v for k, v in call.profile.values.items() if v},
                        "wrapup": w,
                    },
                )
            ).json()
            print(f"\n--- save --- {saved}")
            if saved.get("status") == "saved" and story.get("follow_up_facts"):
                follow = await run_call(
                    http,
                    backend,
                    story["follow_up_facts"],
                    story["follow_up_opening"],
                    4,
                    "follow-up call",
                )

    print("\n--- checklist ---")
    text = " ".join(lines).lower()
    results = [
        check(
            "asked for contact details first",
            bool(lines) and bool(re.search(r"contact number|phone|email", lines[0].lower())),
            lines[0][:80] if lines else "",
        ),
    ]
    repeats = [
        (a, b)
        for i, a in enumerate(lines)
        for b in lines[i + 1 :]
        if difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.8
    ]
    results.append(check("no repeated suggestion", not repeats, repeats[0][0][:60] if repeats else ""))
    results.append(
        check(
            "named a route (TADM / MOM / WICA / TAFEP)",
            bool(re.search(r"tadm|mom\b|wica|tafep", text)),
        )
    )
    results.append(
        check(
            "gave a deadline or timeframe",
            bool(re.search(r"within|deadline|by (mon|tue|wed|thu|fri|sat|sun|\d)", text)),
        )
    )
    results.append(
        check(
            "agreed a follow-up",
            bool(re.search(r"follow up|follow-up|call you|call back|check in", text)),
        )
    )
    results.append(
        check(
            "wrap-up drafted",
            wrap.get("status") == "ok",
            w.get("case", {}).get("action", ""),
        )
    )
    if save:
        results.append(check("case saved", saved.get("status") == "saved", saved.get("case_id", "")))
    if follow:
        case_id = saved.get("case_id")
        found = any(c["case_id"] == case_id for c in follow.history["cases"])
        results.append(check("follow-up call found the saved case", found, case_id or ""))
    llm = call.llm_ms
    if llm:
        print(
            f"  suggestion LLM time: avg {sum(llm) / len(llm) / 1000:.1f}s, max {max(llm) / 1000:.1f}s over {len(llm)} turns"
        )
    return all(results)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--backend", default="http://localhost:8000")
    parser.add_argument("--story", choices=[*STORIES, "all"], default="all")
    parser.add_argument("--save", action="store_true", help="save the wrap-up and run a follow-up call")
    parser.add_argument("--max-rounds", type=int, default=14)
    args = parser.parse_args()
    keys = list(STORIES) if args.story == "all" else [args.story]
    passed = [await run_story(k, args.backend.rstrip("/"), args.save, args.max_rounds) for k in keys]
    print(f"\n{sum(passed)}/{len(passed)} stories passed every check.")


if __name__ == "__main__":
    asyncio.run(main())
