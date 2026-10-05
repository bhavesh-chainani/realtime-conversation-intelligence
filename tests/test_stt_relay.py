from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from backend import stt_relay
from backend.stt_relay import (
    TicketError,
    diarize_turn,
    issue_ticket,
    redeem_ticket,
    unlabelled_turn,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "aai_turn_merged_speakers.json").read_text()
)
ROLE_CHANNEL = {
    "staff": 0,
    "customer": 1,
}  # staff speaks first, so Nemotron's arrival order puts them on 0


def truth_probs(total_ms: int = 22000) -> list[list[float]]:
    frames = [[0.02, 0.02] for _ in range(total_ms // 10)]
    for span in FIXTURE["truth"]:
        for i in range(span["start_ms"] // 10, span["end_ms"] // 10):
            frames[i][ROLE_CHANNEL[span["role"]]] = 0.95
    return frames


# --- tickets ---------------------------------------------------------------


def test_ticket_round_trip_and_single_use():
    ticket = issue_ticket()
    redeem_ticket(ticket)
    with pytest.raises(TicketError):
        redeem_ticket(ticket)


def test_tampered_or_expired_ticket_rejected(monkeypatch):
    payload, _, sig = issue_ticket().partition(".")
    with pytest.raises(TicketError):
        redeem_ticket(f"{payload}.{'0' * len(sig)}")
    monkeypatch.setattr(stt_relay, "TICKET_TTL_SECONDS", -1)
    with pytest.raises(TicketError):
        redeem_ticket(issue_ticket())


# --- turn labelling --------------------------------------------------------


def test_merged_assemblyai_turn_is_split_by_speaker():
    out = diarize_turn(FIXTURE["turn"], truth_probs())
    assert out["diarization"] == "nemotron"
    assert [(s["speaker_label"], s["transcript"]) for s in out["segments"]] == [
        ("A", "This is Bhavesh. May I have your name and NRIC, please?"),
        ("B", "Hi, Bhavesh. My name is Katherine Liao, and my NRIC is S1234567A."),
        ("A", "Right."),
    ]
    assert all(w["speaker"] in {"A", "B"} for w in out["words"])


def test_turn_without_diariser_has_no_speakers():
    out = unlabelled_turn(FIXTURE["turn"])
    assert out["diarization"] == "none"
    assert out["speaker_label"] is None
    assert {w["speaker"] for w in out["words"]} == {None}
    assert len(out["segments"]) == 1 and out["segments"][0]["speaker_label"] is None


# --- relay session ---------------------------------------------------------


class FakeAssemblyAI:
    def __init__(self, messages: list[dict]):
        self.messages = messages
        self.received: list = []
        self.terminated = asyncio.Event()

    async def send(self, data) -> None:
        self.received.append(data)
        if isinstance(data, str) and json.loads(data).get("type") == "Terminate":
            self.terminated.set()

    def __aiter__(self):
        return self._stream()

    async def _stream(self):
        for msg in self.messages:
            yield json.dumps(msg)
        await self.terminated.wait()
        yield json.dumps({"type": "Termination"})


class FakeSession:
    def __init__(self, probs: list[list[float]]):
        self.probs = probs
        self.failed = None
        self.fed = 0

    @property
    def processed_until_ms(self) -> int:
        return len(self.probs) * 10

    def feed(self, pcm: bytes) -> None:
        self.fed += len(pcm)

    def close(self) -> None:
        pass


class FakeDiarizer:
    def __init__(self, session: FakeSession):
        self.session = session

    def new_session(self, sample_rate: int) -> FakeSession:
        return self.session


def aai_messages() -> list[dict]:
    # Without speaker_labels AssemblyAI sends no speakers; strip the fixture's to match.
    turn = {k: v for k, v in FIXTURE["turn"].items() if k != "speaker_label"}
    turn["words"] = [
        {k: v for k, v in w.items() if k != "speaker"} for w in turn["words"]
    ]
    return [
        {"type": "Begin", "id": "s1"},
        {
            "type": "Turn",
            "turn_order": 1,
            "end_of_turn": False,
            "transcript": "This is",
            "words": [],
        },
        {**turn, "turn_is_formatted": False},
        turn,
        turn,  # a re-sent final is dropped
    ]


def run_session(
    client, monkeypatch, session: FakeSession | None
) -> tuple[list[dict], FakeAssemblyAI]:
    fake = FakeAssemblyAI(aai_messages())

    @asynccontextmanager
    async def connect(sample_rate):
        assert sample_rate == 48000
        yield fake

    monkeypatch.setattr(stt_relay.assemblyai, "connect", connect)
    monkeypatch.setattr(
        stt_relay, "get_diarizer", lambda: FakeDiarizer(session) if session else None
    )
    received = []
    with client.websocket_connect(
        f"/ws/stt?ticket={issue_ticket()}&sample_rate=48000"
    ) as ws:
        while True:
            msg = ws.receive_json()
            received.append(msg)
            if msg["type"] == "Turn" and msg.get("end_of_turn"):
                break
        ws.send_bytes(b"\x01\x00" * 800)
        ws.send_text(json.dumps({"type": "Terminate"}))
        assert ws.receive_json()["type"] == "Termination"
    return received, fake


def test_relay_holds_final_turn_then_sends_speaker_segments(client, monkeypatch):
    session = FakeSession(truth_probs())
    received, fake = run_session(client, monkeypatch, session)

    kinds = [m["type"] for m in received]
    assert kinds == [
        "Begin",
        "Turn",
        "PendingTurn",
        "Turn",
    ]  # unformatted and re-sent finals dropped
    final = received[-1]
    assert final["diarization"] == "nemotron"
    assert [s["speaker_label"] for s in final["segments"]] == ["A", "B", "A"]
    # the same audio reached both AssemblyAI and the diariser
    assert b"\x01\x00" * 800 in fake.received and session.fed == 1600


def test_relay_sends_partial_labels_when_diariser_lags(client, monkeypatch):
    monkeypatch.setattr(stt_relay.cfg, "DIARIZATION_MAX_WAIT_MS", 100)
    received, _ = run_session(client, monkeypatch, FakeSession(truth_probs()[:1000]))
    final = received[-1]
    assert final["diarization"] == "partial"
    assert (
        final["segments"][0]["speaker_label"] == "A"
    )  # the words the diariser did hear


def test_relay_without_diariser_sends_unlabelled_turns(client, monkeypatch):
    received, _ = run_session(client, monkeypatch, None)
    assert received[-1]["diarization"] == "none"
    assert received[-1]["speaker_label"] is None


def test_terminate_still_delivers_turns_waiting_for_labels(client, monkeypatch):
    monkeypatch.setattr(stt_relay.cfg, "DIARIZATION_MAX_WAIT_MS", 300)
    fake = FakeAssemblyAI(aai_messages())

    @asynccontextmanager
    async def connect(sample_rate):
        yield fake

    monkeypatch.setattr(stt_relay.assemblyai, "connect", connect)
    monkeypatch.setattr(
        stt_relay, "get_diarizer", lambda: FakeDiarizer(FakeSession([]))
    )
    with client.websocket_connect(f"/ws/stt?ticket={issue_ticket()}") as ws:
        while ws.receive_json()["type"] != "PendingTurn":
            pass
        ws.send_text(json.dumps({"type": "Terminate"}))
        kinds = [ws.receive_json()["type"], ws.receive_json()["type"]]
    assert kinds == ["Turn", "Termination"]


def test_relay_rejects_bad_ticket(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/stt?ticket=nope") as ws:
            ws.receive_json()
    assert exc.value.code == 1008
