"""POST /assist: runs both agents for one customer turn and streams the results as NDJSON events.

Order for each turn:
1. The instant regex reads the latest customer line and, when it hears an NRIC or full name, looks the
   caller up in the customer DB straight away (`customer` and `history` events within milliseconds).
2. A short debounce: the browser aborts this request when the next fragment arrives, so a turn that
   is still being spoken costs no LLM call.
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
from .agents.entity_agent import extract_entities
from .agents.suggestion_agent import suggest_with_fallback
from .customer_history import CustomerCase, is_open_case_status
from .profile import Profile, Source, cases_key, next_lookup, records_prefill
from .quick_entities import quick_patch

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


class AssistRequest(BaseModel):
    turns: list[AssistTurn]
    customer: dict[str, str] = {}
    sources: dict[str, Source] = {}
    history: HistoryState | None = None
    extract: bool = Field(
        True, description="False when re-suggesting after a manual lookup"
    )
    max_suggestions: int | None = None


@dataclass(frozen=True)
class AssistDeps:
    lookup: Callable[
        [str | None, str | None], dict[str, Any]
    ]  # blocking; run in a thread
    extract: Callable[[str], Awaitable[dict[str, str | None]]]
    suggest: Callable[..., Awaitable[dict[str, Any]]]
    debounce_s: float = 0.25
    lookup_wait_s: float = (
        0.7  # how long past the debounce the first suggestion waits for a lookup
    )


def default_deps() -> AssistDeps:
    from . import customer_history

    return AssistDeps(
        # Read the service at call time so tests can replace it.
        lookup=lambda name, nric: customer_history.customer_history_service.lookup(
            name, nric
        ),
        extract=extract_entities,
        suggest=suggest_with_fallback,
    )


def format_transcript(turns: list[AssistTurn], window: int | None = None) -> str:
    """Role-labelled transcript; with `window`, only the latest turns plus a count of the rest."""
    shown = turns[-window:] if window else turns
    body = "\n".join(
        f"{ROLE_LABELS[t.role]}: {t.text.strip()}" for t in shown if t.text.strip()
    )
    omitted = len(turns) - len(shown)
    return f"[Earlier: {omitted} turns omitted]\n{body}" if omitted > 0 else body


def _customer_event(source: Source, patch: dict[str, str]) -> dict[str, Any]:
    return {"type": "customer", "source": source, "patch": patch}


def _error_event(stage: str, exc: BaseException) -> dict[str, Any]:
    logger.warning("[assist] %s failed: %s: %s", stage, type(exc).__name__, exc)
    return {"type": "error", "stage": stage, "message": str(exc) or type(exc).__name__}


def _cases(result: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            k: str(row.get(k) or "")
            for k in ("case_id", "company", "type", "status", "summary")
        }
        for row in result.get("cases") or []
        if isinstance(row, dict)
    ]


class _AssistRun:
    def __init__(self, req: AssistRequest, deps: AssistDeps):
        self.req, self.deps = req, deps
        self.turns = req.turns[-MAX_TURNS:]
        self.profile = Profile.from_request(req.customer, req.sources)
        history = req.history or HistoryState()
        self.lookup_key = (
            history.lookup_key
        )  # last identity looked up (or being looked up)
        self.match = history.match_strategy
        self.cases = [c.model_dump() for c in history.cases]
        self.tasks: dict[asyncio.Task, str] = {}
        self.lookup_keys: dict[asyncio.Task, str] = {}
        self.lookups = 0
        self.suggest_task: asyncio.Task | None = None
        self.suggest_round = 0
        self.suggest_key: str | None = None

    # --- lookup ---------------------------------------------------------------------------

    def start_lookup(self) -> dict[str, Any] | None:
        if self.lookups >= MAX_LOOKUPS:
            return None
        values = self.profile.values
        request = next_lookup(
            self.lookup_key, values["name"], values["nric_worker_permit_id"]
        )
        if not request:
            return None
        self.lookups += 1
        self.lookup_key = request.key
        task = asyncio.create_task(
            asyncio.to_thread(self.deps.lookup, request.name, request.nric)
        )
        self.tasks[task] = "lookup"
        self.lookup_keys[task] = request.key
        return {"type": "history", "status": "loading", "lookup_key": request.key}

    def finish_lookup(self, task: asyncio.Task) -> list[dict[str, Any]]:
        key = self.lookup_keys.pop(task)
        try:
            result = task.result()
        except Exception as exc:
            result = {
                "status": "error",
                "message": "Unable to obtain customer history at the moment.",
            }
            events = [_error_event("lookup", exc)]
        else:
            events = []
        status = result.get("status") or "error"
        ok = status == "ok"
        self.match = result.get("match_strategy") if ok else None
        self.cases = _cases(result) if ok else []
        events.append(
            {
                "type": "history",
                "status": status,
                "lookup_key": key,
                "match_strategy": self.match,
                "cases": self.cases,
                "open_count": (
                    result.get("open_count")
                    if isinstance(result.get("open_count"), int)
                    else sum(is_open_case_status(c["status"]) for c in self.cases)
                ),
                "summary": result.get("history_summary")
                or result.get("message")
                or "No customer history found.",
            }
        )
        prefill = records_prefill(result)
        if prefill and (accepted := self.profile.apply(prefill, "records")):
            events.append(_customer_event("records", accepted))
        return events

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
        self.suggest_key = cases_key(self.match, self.cases)
        self.suggest_task = asyncio.create_task(
            self.deps.suggest(
                context,
                self.req.max_suggestions or cfg.SUGGESTION_MAX,
                self.profile.suggestion_payload(self.match),
                list(self.cases),
            )
        )
        self.tasks[self.suggest_task] = "suggest"
        return {"type": "suggesting", "round": self.suggest_round}

    def records_changed(self) -> bool:
        return (
            self.suggest_key is not None
            and cases_key(self.match, self.cases) != self.suggest_key
        )

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

    def finish_suggest(self, task: asyncio.Task, round_: int) -> dict[str, Any]:
        try:
            body = task.result()
        except Exception as exc:
            return _error_event("suggest", exc)
        return {
            "type": "suggestions",
            "round": round_,
            "suggestions": body.get("suggestions") or [],
            "fallback": bool(body.get("fallback")),
            "timings": body.get("timings") or {},
        }

    # --- the turn -------------------------------------------------------------------------

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        started = time.perf_counter()
        try:
            trigger = next(
                (t.text for t in reversed(self.turns) if t.role != "staff"), ""
            )
            if heard := self.profile.apply(quick_patch(trigger), "heard"):
                yield _customer_event("heard", heard)
            if loading := self.start_lookup():
                yield loading

            # The DB result goes out as soon as it lands; the LLM calls wait out the debounce and up to
            # lookup_wait_s for the record, so the first suggestion can use it.
            debounce_end = time.perf_counter() + self.deps.debounce_s
            lookups = [t for t, kind in self.tasks.items() if kind == "lookup"]
            if lookups:
                await asyncio.wait(
                    lookups, timeout=self.deps.debounce_s + self.deps.lookup_wait_s
                )
                for task in lookups:
                    if task.done():
                        del self.tasks[task]
                        for event in self.finish_lookup(task):
                            yield event
            if (remaining := debounce_end - time.perf_counter()) > 0:
                await asyncio.sleep(remaining)

            if self.req.extract and self.profile.missing():
                self.tasks[
                    asyncio.create_task(
                        self.deps.extract(format_transcript(self.turns))
                    )
                ] = "extract"
            if suggesting := self.start_suggest():
                yield suggesting

            while self.tasks:
                done, _ = await asyncio.wait(
                    self.tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    kind = self.tasks.pop(task, None)
                    if kind == "lookup":
                        for event in self.finish_lookup(task):
                            yield event
                        if self.records_changed() and (
                            suggesting := self.start_suggest()
                        ):
                            yield suggesting
                    elif kind == "extract":
                        for event in self.finish_extract(task):
                            yield event
                    elif kind == "suggest" and not task.cancelled():
                        yield self.finish_suggest(task, self.suggest_round)
        except Exception as exc:
            yield _error_event("assist", exc)
        finally:
            for task in self.tasks:
                task.cancel()
        yield {
            "type": "done",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }


def run_assist(req: AssistRequest, deps: AssistDeps) -> AsyncIterator[dict[str, Any]]:
    return _AssistRun(req, deps).events()


def get_assist_deps() -> AssistDeps:
    return default_deps()


@router.post("/assist")
async def assist(
    req: AssistRequest, deps: AssistDeps = Depends(get_assist_deps)
) -> StreamingResponse:
    async def lines() -> AsyncIterator[str]:
        async for event in run_assist(req, deps):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
