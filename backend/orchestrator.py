"""POST /assist: runs both agents for one customer turn and streams the results as NDJSON events.

Order for each turn:
1. The instant regex reads the latest customer line and, when it hears a phone number, email or full name,
   looks the caller up in the customer DB straight away (`customer` and `history` events within milliseconds).
2. No debounce: a newer turn can only arrive after more speech and silence, and the browser aborts
   this request when it does (which cancels its LLM calls). The first suggestion waits up to
   `lookup_wait_s` for a lookup started on this turn, so it can use the record.
3. The entity agent (LLM extraction, only while fields are missing) runs alongside the suggestion
   agent. If extraction reveals a new identity whose DB record differs, the suggestion is redone with
   the new record (`round` 2).

Events, one JSON object per line:
  {"type": "customer", "source": "heard" | "ai" | "records", "patch": {field: value}}
  {"type": "history", "status": "loading", "lookup_key": ...}
  {"type": "history", "status": "ok" | "not_found" | "invalid_input" | "not_configured" | "error",
   "lookup_key", "match_strategy", "cases", "open_count", "summary"}
  {"type": "suggesting", "round": n}
  {"type": "suggestions", "round": n, "suggestions": [...], "fallback": bool, "timings": {...}}
  {"type": "error", "stage": "lookup" | "extract" | "suggest" | "assist", "message": ...}
  {"type": "done", "elapsed_ms": ...}   always last, unless the client went away
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import config as cfg
from . import customer_history
from .agents.entity_agent import extract_entities
from .agents.suggestion_agent import suggest_with_fallback
from .customer_history import CustomerCase, case_rows
from .profile import (
    Profile,
    Source,
    cases_key,
    identity_key,
    next_lookup,
    records_prefill,
)
from .quick_entities import quick_patch, quick_patch_from_lines

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_TURNS = 200
SUGGESTION_WINDOW_TURNS = 16
MAX_LOOKUPS = 2
MAX_SUGGESTION_ROUNDS = 3
ROLE_LABELS = {"staff": "Staff", "customer": "Customer", "unknown": "Unknown"}


class AssistTurn(BaseModel):
    role: Literal["staff", "customer", "unknown"]
    text: str


class HistoryState(BaseModel):
    """The last lookup result the client holds, so the backend stays stateless between turns."""

    lookup_key: str | None = None
    match_strategy: str | None = None
    cases: list[CustomerCase] = []
    status: str | None = None  # the client's history status: "loading", "not_found", "error", ...


class AssistRequest(BaseModel):
    turns: list[AssistTurn]
    customer: dict[str, str] = {}
    sources: dict[str, Source] = {}
    history: HistoryState | None = None
    extract: bool = Field(True, description="False when re-suggesting after a manual lookup")
    max_suggestions: int | None = None
    previous_suggestions: list[str] = Field(
        [],
        description="What Staff currently see, so the agent can move on instead of repeating it",
    )


@dataclass(frozen=True)
class AssistDeps:
    lookup: Callable[
        [str | None, str | None, str | None], dict[str, Any]
    ]  # (name, phone, email); blocking, run in a thread
    extract: Callable[[str], Awaitable[dict[str, str | None]]]
    suggest: Callable[..., Awaitable[dict[str, Any]]]
    lookup_wait_s: float = 0.7  # how long the first suggestion waits for a lookup started this turn


def default_deps() -> AssistDeps:
    return AssistDeps(
        lookup=customer_history.customer_history_service.lookup,
        extract=extract_entities,
        suggest=suggest_with_fallback,
    )


def format_transcript(turns: list[AssistTurn], window: int | None = None) -> str:
    """Role-labelled transcript; with `window`, only the latest turns plus a count of the rest."""
    shown = turns[-window:] if window else turns
    body = "\n".join(f"{ROLE_LABELS[t.role]}: {t.text.strip()}" for t in shown if t.text.strip())
    omitted = len(turns) - len(shown)
    return f"[Earlier: {omitted} turns omitted]\n{body}" if omitted > 0 else body


def _customer_event(source: Source, patch: dict[str, str]) -> dict[str, Any]:
    return {"type": "customer", "source": source, "patch": patch}


def _error_event(stage: str, exc: BaseException) -> dict[str, Any]:
    logger.warning("[assist] %s failed: %s: %s", stage, type(exc).__name__, exc)
    return {"type": "error", "stage": stage, "message": str(exc) or type(exc).__name__}


class _AssistRun:
    def __init__(self, req: AssistRequest, deps: AssistDeps):
        self.req, self.deps = req, deps
        self.turns = req.turns[-MAX_TURNS:]
        self.profile = Profile.from_request(req.customer, req.sources)
        history = req.history or HistoryState()
        self.lookup_key = history.lookup_key  # last identity looked up (or being looked up)
        self.match = history.match_strategy
        self.cases = case_rows([c.model_dump() for c in history.cases])
        self.history_status = history.status
        self.previous = [s.strip()[:400] for s in req.previous_suggestions if s.strip()][:3]
        self.tasks: dict[asyncio.Task, str] = {}
        self.lookup_keys: dict[asyncio.Task, str] = {}
        self.lookups = 0
        self.suggest_task: asyncio.Task | None = None
        self.suggest_round = 0
        self.suggest_key: str | None = None
        self.started = time.perf_counter()
        self.timeline: dict[str, float] = {}  # ms since the request arrived, for the per-turn log

    # --- lookup ---------------------------------------------------------------------------

    def start_lookup(self) -> dict[str, Any] | None:
        if self.lookups >= MAX_LOOKUPS:
            return None
        values = self.profile.values
        request = next_lookup(self.lookup_key, values["name"], values["contact_number"], values["email"])
        if not request:
            return None
        self.lookups += 1
        self.lookup_key = request.key
        task = asyncio.create_task(
            asyncio.to_thread(self.deps.lookup, request.name, request.phone, request.email)
        )
        self.tasks[task] = "lookup"
        self.lookup_keys[task] = request.key
        return {"type": "history", "status": "loading", "lookup_key": request.key}

    def finish_lookup(self, task: asyncio.Task) -> list[dict[str, Any]]:
        key = self.lookup_keys.pop(task)
        try:
            result = task.result()
        except Exception as exc:
            result = {"status": "error", "summary": "Unable to obtain customer history at the moment."}
            events = [_error_event("lookup", exc)]
        else:
            events = []
        prefill = records_prefill(result)
        accepted = self.profile.apply(prefill, "records") if prefill else {}
        if prefill and key == self.lookup_key:
            # The record may add the other contact detail; key on what the card now holds so the next
            # turn does not look the same caller up again.
            values = self.profile.values
            key = identity_key(values["contact_number"], values["email"]) or key
            self.lookup_key = key
        status = result.get("status") or "error"
        self.history_status = status
        ok = status == "ok"
        self.match = result.get("match_strategy") if ok else None
        self.cases = case_rows(result.get("cases")) if ok else []
        events.append(
            {
                "type": "history",
                "status": status,
                "lookup_key": key,
                "match_strategy": self.match,
                "cases": self.cases,
                "open_count": result.get("open_count", 0) if ok else 0,
                "summary": result.get("summary") or "No customer history found.",
            }
        )
        if accepted:
            events.append(_customer_event("records", accepted))
        return events

    def heard_patch(self, trigger: str) -> dict[str, str]:
        """Identity heard in the latest line, plus any field still blank that an earlier customer line
        gave (e.g. a name said two turns before the phone number)."""
        earlier = quick_patch_from_lines([t.text for t in self.turns if t.role == "customer"])
        blank = {k: v for k, v in earlier.items() if not self.profile.values[k]}
        return {**blank, **quick_patch(trigger)}

    def lookup_status(self) -> str:
        """Where the history check stands, as the suggestion agent should see it."""
        if "lookup" in self.tasks.values() or self.history_status == "loading":
            return "pending"
        if self.match:
            return "name" if self.match == "name" else "verified"
        if self.history_status in ("error", "not_configured"):
            return "unavailable"
        return "not_found" if self.lookup_key else "not_started"

    # --- agents ---------------------------------------------------------------------------

    def start_suggest(self) -> dict[str, Any] | None:
        context = format_transcript(self.turns, SUGGESTION_WINDOW_TURNS)
        if len(context) < 10 or self.suggest_round >= MAX_SUGGESTION_ROUNDS:
            return None
        if self.suggest_task and not self.suggest_task.done():
            # A newer customer record arrived: the answer in flight is already out of date.
            self.suggest_task.cancel()
            self.tasks.pop(self.suggest_task, None)
        self.suggest_round += 1
        self.timeline.setdefault("suggest_started", self._elapsed_ms())
        self.suggest_key = cases_key(self.match, self.cases)
        self.suggest_task = asyncio.create_task(
            self.deps.suggest(
                context,
                self.req.max_suggestions or cfg.SUGGESTION_MAX,
                self.profile.suggestion_payload(self.match, self.lookup_status()),
                list(self.cases),
                list(self.previous),
            )
        )
        self.tasks[self.suggest_task] = "suggest"
        return {"type": "suggesting", "round": self.suggest_round}

    def records_changed(self) -> bool:
        return self.suggest_key is not None and cases_key(self.match, self.cases) != self.suggest_key

    def finish_extract(self, task: asyncio.Task) -> list[dict[str, Any]]:
        try:
            found = task.result()
        except Exception as exc:
            return [_error_event("extract", exc)]
        events = []
        if accepted := self.profile.apply(found, "ai"):
            events.append(_customer_event("ai", accepted))
        if loading := self.start_lookup():
            events.append(loading)
        return events

    def _elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000)

    def finish_suggest(self, task: asyncio.Task) -> dict[str, Any]:
        try:
            body = task.result()
        except Exception as exc:
            return _error_event("suggest", exc)
        self.timeline.setdefault("suggestion", self._elapsed_ms())
        return {
            "type": "suggestions",
            "round": self.suggest_round,
            "suggestions": body.get("suggestions") or [],
            "fallback": bool(body.get("fallback")),
            "timings": body.get("timings") or {},
        }

    # --- the turn -------------------------------------------------------------------------

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        try:
            trigger = next((t.text for t in reversed(self.turns) if t.role != "staff"), "")
            if heard := self.profile.apply(self.heard_patch(trigger), "heard"):
                yield _customer_event("heard", heard)
            if loading := self.start_lookup():
                yield loading

            # The DB result goes out as soon as it lands (a few ms); the first suggestion waits up to
            # lookup_wait_s for it, so it can use the record.
            lookups = [t for t, kind in self.tasks.items() if kind == "lookup"]
            if lookups:
                await asyncio.wait(lookups, timeout=self.deps.lookup_wait_s)
                for task in lookups:
                    if task.done():
                        del self.tasks[task]
                        for event in self.finish_lookup(task):
                            yield event

            # Suggestion first: it is what Staff wait for, and it should get the warm gateway connection.
            if suggesting := self.start_suggest():
                yield suggesting
            if self.req.extract and self.profile.missing():
                window = format_transcript(self.turns, SUGGESTION_WINDOW_TURNS)
                self.tasks[asyncio.create_task(self.deps.extract(window))] = "extract"

            while self.tasks:
                done, _ = await asyncio.wait(self.tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    kind = self.tasks.pop(task, None)
                    if kind == "lookup":
                        for event in self.finish_lookup(task):
                            yield event
                        if self.records_changed() and (suggesting := self.start_suggest()):
                            yield suggesting
                    elif kind == "extract":
                        for event in self.finish_extract(task):
                            yield event
                    elif kind == "suggest" and not task.cancelled():
                        yield self.finish_suggest(task)
        except Exception as exc:
            yield _error_event("assist", exc)
        finally:
            for task in self.tasks:
                task.cancel()
        elapsed = self._elapsed_ms()
        logger.info(
            "[assist] turn: suggestion started +%sms, shown +%sms, done +%sms (%s rounds)",
            self.timeline.get("suggest_started", "-"),
            self.timeline.get("suggestion", "-"),
            elapsed,
            self.suggest_round,
        )
        yield {"type": "done", "elapsed_ms": elapsed}


def run_assist(req: AssistRequest, deps: AssistDeps) -> AsyncIterator[dict[str, Any]]:
    return _AssistRun(req, deps).events()


@router.post("/assist")
async def assist(req: AssistRequest, deps: AssistDeps = Depends(default_deps)) -> StreamingResponse:
    async def lines() -> AsyncIterator[str]:
        async for event in run_assist(req, deps):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
