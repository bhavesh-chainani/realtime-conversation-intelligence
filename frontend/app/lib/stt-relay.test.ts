import assert from "node:assert/strict";
import { test } from "node:test";

import { normalizeSpeakerLabel, parsePendingTurn, parseRelayInfo, parseRelaySegments, relayUrl } from "./stt-relay.ts";

test("empty labels are not speakers", () => {
  assert.equal(normalizeSpeakerLabel(" unknown "), null);
  assert.equal(normalizeSpeakerLabel(null), null);
  assert.equal(normalizeSpeakerLabel("b"), "B");
});

test("session payload becomes a relay URL", () => {
  assert.equal(parseRelayInfo({ token: "abc" }), null);
  const relay = parseRelayInfo({ path: "/ws/stt", ticket: "t.sig" });
  assert.deepEqual(relay, { path: "/ws/stt", ticket: "t.sig" });
  assert.equal(
    relayUrl("https://api.example.com/", relay!, 48000),
    "wss://api.example.com/ws/stt?ticket=t.sig&sample_rate=48000"
  );
  assert.equal(
    relayUrl("http://localhost:8000", relay!, 44100),
    "ws://localhost:8000/ws/stt?ticket=t.sig&sample_rate=44100"
  );
});

test("pending turn carries its text", () => {
  assert.deepEqual(parsePendingTurn({ type: "PendingTurn", turn_order: 3, transcript: " Hi. " }), {
    turnOrder: 3,
    text: "Hi.",
  });
  assert.equal(parsePendingTurn({ type: "Turn" }), null);
});

test("a merged turn becomes one segment per speaker", () => {
  const segments = parseRelaySegments({
    type: "Turn",
    end_of_turn: true,
    segments: [
      { speaker_label: "A", transcript: "May I have your phone number?" },
      { speaker_label: "B", transcript: "It's 9123 4567." },
      { speaker_label: null, transcript: "Right." },
      { speaker_label: "B", transcript: "" },
    ],
  });
  assert.deepEqual(segments, [
    { text: "May I have your phone number?", speakerLabel: "A" },
    { text: "It's 9123 4567.", speakerLabel: "B" },
    { text: "Right.", speakerLabel: null },
  ]);
  assert.equal(parseRelaySegments({ type: "Turn", end_of_turn: true }), null);
});
