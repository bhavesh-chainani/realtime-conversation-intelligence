"""Live STT relay: browser audio -> AssemblyAI (words) + Nemotron (who said each word).

The browser sends raw Int16 PCM to /ws/stt. Every byte goes to both AssemblyAI and the
diariser, so AssemblyAI word times and diariser frames share one clock (the stream start).
Messages back to the browser are AssemblyAI-shaped:

- partial `Turn` messages pass straight through, without speakers
- a finished turn is announced at once as `PendingTurn`, then held until the diariser has
  covered its last word (or `DIARIZATION_MAX_WAIT_MS` passes), then sent as a final `Turn`
  whose `segments` split it wherever the speaker changes. `diarization` says how complete
  the labels are: "nemotron" (fully covered), "partial" (the diariser was still behind) or
  "none" (no diariser loaded: every speaker is null and the operator assigns roles)

Speaker labels are "A", "B", ... in Nemotron's order of first arrival.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import assemblyai
from . import config as cfg
from .diarization.merge import majority, smooth, split_by_speaker, word_speakers
from .diarization.nemotron import get_diarizer

logger = logging.getLogger(__name__)

router = APIRouter()

TICKET_TTL_SECONDS = 300
# Diariser coverage needed past a turn's last word before its labels are trusted.
END_MARGIN_MS = 50

# ---------------------------------------------------------------------------
# One-time tickets: a browser WebSocket cannot send an Authorization header, so the
# authenticated token endpoint hands out a short-lived signed ticket instead.
# ---------------------------------------------------------------------------

_redeemed: dict[str, float] = {}


class TicketError(Exception):
    pass


def _sign(payload: bytes) -> str:
    return hmac.new(cfg.STT_TICKET_SECRET.encode(), payload, hashlib.sha256).hexdigest()


def issue_ticket() -> str:
    body = json.dumps({"e": int(time.time()) + TICKET_TTL_SECONDS, "n": secrets.token_hex(8)})
    payload = base64.urlsafe_b64encode(body.encode())
    return f"{payload.decode()}.{_sign(payload)}"


def redeem_ticket(ticket: str) -> None:
    """Raises TicketError unless the ticket is valid and unused. Each ticket opens one session."""
    payload, _, signature = ticket.partition(".")
    if not signature or not hmac.compare_digest(signature, _sign(payload.encode())):
        raise TicketError("bad signature")
    data = json.loads(base64.urlsafe_b64decode(payload))
    now = time.time()
    if data["e"] < now:
        raise TicketError("expired")
    for nonce in [n for n, exp in _redeemed.items() if exp < now]:
        del _redeemed[nonce]
    if data["n"] in _redeemed:
        raise TicketError("already used")
    _redeemed[data["n"]] = data["e"]


# ---------------------------------------------------------------------------
# Speaker labels
# ---------------------------------------------------------------------------


def _labelled_turn(msg: dict, labels: list[str | None], source: str) -> dict:
    words = [{**w, "speaker": label} for w, label in zip(msg.get("words") or [], labels, strict=False)]
    if words:
        segments = [
            {"speaker_label": seg["speaker"], "transcript": seg["text"], "words": seg["words"]}
            for seg in split_by_speaker(words, labels)
        ]
    else:
        segments = [{"speaker_label": None, "transcript": msg.get("transcript", ""), "words": []}]
    return {
        **msg,
        "words": words,
        "speaker_label": majority(labels),
        "segments": segments,
        "diarization": source,
    }


def diarize_turn(msg: dict, probs: list[list[float]], source: str = "nemotron") -> dict:
    """Label each word with its Nemotron speaker. Words past the diariser's coverage take the
    nearest active frame, or None."""
    words = msg.get("words") or []
    labels = [None if s is None else chr(ord("A") + s) for s in smooth(word_speakers(words, probs))]
    return _labelled_turn(msg, labels, source)


def unlabelled_turn(msg: dict) -> dict:
    return _labelled_turn(msg, [None] * len(msg.get("words") or []), "none")


# ---------------------------------------------------------------------------
# Relay
# ---------------------------------------------------------------------------


def _origin_allowed(origin: str | None) -> bool:
    return origin is None or "*" in cfg.BACKEND_CORS_ORIGINS or origin in cfg.BACKEND_CORS_ORIGINS


class Relay:
    def __init__(self, browser: WebSocket, aai: Any, diar: Any | None, max_wait_ms: int):
        self.browser, self.aai, self.diar = browser, aai, diar
        self.max_wait = max_wait_ms / 1000
        # turn_order -> latest formatted final, in case AssemblyAI re-sends a turn.
        self.held: dict[Any, dict] = {}
        self.queue: asyncio.Queue[tuple[Any, float]] = asyncio.Queue()
        self.sent: set[Any] = set()
        self.unsent = 0  # turns announced as pending and not yet sent final
        self.stats = {"nemotron": 0, "partial": 0, "none": 0}

    async def run(self) -> None:
        upstream, downstream = asyncio.create_task(self._upstream()), asyncio.create_task(self._downstream())
        emitter = asyncio.create_task(self._emit_finals())
        try:
            done, _ = await asyncio.wait({upstream, downstream}, return_when=asyncio.FIRST_COMPLETED)
            if upstream in done and upstream.result() == "terminate":
                # The caller ended the stream: let AssemblyAI finish the last turn, send the held
                # turns, then confirm with Termination.
                if await asyncio.wait_for(downstream, timeout=15):
                    deadline = time.monotonic() + self.max_wait + 1
                    if self.diar is not None and self.unsent:
                        # Diarise the audio tail too short to fill a streaming chunk.
                        with suppress(asyncio.TimeoutError):
                            await asyncio.wait_for(asyncio.to_thread(self.diar.close), timeout=self.max_wait)
                    while self.unsent and time.monotonic() < deadline:
                        await asyncio.sleep(0.05)
                    await self.browser.send_json({"type": "Termination"})
                    await self.browser.close()
            else:
                for task in done:
                    task.result()
        finally:
            for task in (upstream, downstream, emitter):
                task.cancel()
            logger.info("STT relay closed: turns diarised=%s", self.stats)

    async def _upstream(self) -> str:
        """Forward caller audio until it disconnects ("disconnect") or sends Terminate ("terminate")."""
        reason = "disconnect"
        try:
            while True:
                msg = await self.browser.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    await self.aai.send(msg["bytes"])
                    if self.diar is not None:
                        self.diar.feed(msg["bytes"])
                elif msg.get("text") and json.loads(msg["text"]).get("type") == "Terminate":
                    reason = "terminate"
                    break
        except WebSocketDisconnect:
            pass
        try:
            await self.aai.send(json.dumps({"type": "Terminate"}))
        except Exception:
            pass
        return reason

    async def _downstream(self) -> bool:
        """Forward AssemblyAI messages; True once AssemblyAI confirms the end of the stream."""
        async for raw in self.aai:
            msg = json.loads(raw)
            if msg.get("type") == "Termination":
                return True  # forwarded by run() once the held turns are out
            if msg.get("type") != "Turn":
                await self.browser.send_json(msg)
                continue
            if not msg.get("end_of_turn"):
                await self.browser.send_json(msg)
                continue
            if msg.get("turn_is_formatted") is False:
                continue  # the formatted version follows
            order = msg.get("turn_order")
            if order in self.sent:
                continue
            if order in self.held:
                self.held[order] = msg
                continue
            self.held[order] = msg
            self.unsent += 1
            self.queue.put_nowait((order, time.monotonic()))
            await self.browser.send_json(
                {
                    "type": "PendingTurn",
                    "turn_order": msg.get("turn_order"),
                    "transcript": msg.get("transcript", ""),
                }
            )
        await self.browser.close(code=1011, reason="AssemblyAI closed the stream")
        return False

    async def _emit_finals(self) -> None:
        while True:
            order, received = await self.queue.get()
            words = self.held[order].get("words") or []
            need_ms = max((w.get("end") or 0) for w in words) + END_MARGIN_MS if words else 0
            diar = self.diar
            while (
                diar is not None
                and not diar.failed
                and diar.processed_until_ms < need_ms
                and time.monotonic() - received < self.max_wait
            ):
                await asyncio.sleep(0.05)
            msg = self.held.pop(order)
            self.sent.add(order)
            if diar is None or diar.failed:
                out = unlabelled_turn(msg)
            elif diar.processed_until_ms >= need_ms:
                out = diarize_turn(msg, diar.probs)
            else:
                out = diarize_turn(msg, diar.probs, source="partial")
                logger.warning(
                    "Turn %s sent before the diariser caught up (at %s ms, needed %s ms)",
                    msg.get("turn_order"),
                    diar.processed_until_ms,
                    need_ms,
                )
            self.stats[out["diarization"]] += 1
            await self.browser.send_json(out)
            self.unsent -= 1


@router.websocket("/ws/stt")
async def stt_relay(ws: WebSocket, ticket: str = "", sample_rate: int = 48000) -> None:
    if not _origin_allowed(ws.headers.get("origin")):
        await ws.close(code=1008, reason="origin not allowed")
        return
    try:
        redeem_ticket(ticket)
    except Exception as exc:  # bad / used / expired ticket
        logger.warning("STT relay rejected: %s", exc)
        await ws.close(code=1008, reason="invalid ticket")
        return
    await ws.accept()

    diarizer = get_diarizer()
    diar = diarizer.new_session(sample_rate) if diarizer else None
    try:
        async with assemblyai.connect(sample_rate) as aai:
            await Relay(ws, aai, diar, cfg.DIARIZATION_MAX_WAIT_MS).run()
    except Exception as exc:
        logger.exception("STT relay failed")
        try:
            await ws.close(code=1011, reason=str(exc)[:100])
        except Exception:
            pass
