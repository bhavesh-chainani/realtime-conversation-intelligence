import assert from "node:assert/strict";
import { test } from "node:test";

import {
  normalizeSpeakerLabel,
  parsePendingTurn,
  parseRelayInfo,
  parseRelaySegments,
  relayUrl,
} from "./stt-relay.ts";

test("PENDING and empty labels are not speakers", () => {
  assert.equal(normalizeSpeakerLabel("PENDING"), null);
  assert.equal(normalizeSpeakerLabel(" unknown "), null);
  assert.equal(normalizeSpeakerLabel(null), null);
  assert.equal(normalizeSpeakerLabel("b"), "B");
});

test("relay is used only when the token endpoint offers one", () => {
  assert.equal(parseRelayInfo({ token: "abc" }), null);
  const relay = parseRelayInfo({ relay: { path: "/ws/stt", ticket: "t.sig" } });
  assert.deepEqual(relay, { path: "/ws/stt", ticket: "t.sig" });
  assert.equal(relayUrl("https://api.example.com/", relay!, 48000), "wss://api.example.com/ws/stt?ticket=t.sig&sample_rate=48000");
  assert.equal(relayUrl("http://localhost:8000", relay!, 44100), "ws://localhost:8000/ws/stt?ticket=t.sig&sample_rate=44100");
});

test("pending turn carries text and provisional label", () => {
  assert.deepEqual(parsePendingTurn({ type: "PendingTurn", turn_order: 3, transcript: " Hi. ", speaker_label: "a" }), {
    turnOrder: 3,
    text: "Hi.",
    speakerLabel: "A",
  });
  assert.equal(parsePendingTurn({ type: "Turn" }), null);
});

test("a merged turn becomes one segment per speaker, with word labels", () => {
  const segments = parseRelaySegments({
    type: "Turn",
    end_of_turn: true,
    segments: [
      { speaker_label: "A", transcript: "May I have your NRIC?", words: [{ speaker: "A" }, { speaker: "A" }] },
      { speaker_label: "B", transcript: "It's S1234567A.", words: [{ speaker: "B" }] },
      { speaker_label: "B", transcript: "", words: [] },
    ],
  });
  assert.deepEqual(segments, [
    { text: "May I have your NRIC?", speakerLabel: "A", wordLabels: ["A", "A"] },
    { text: "It's S1234567A.", speakerLabel: "B", wordLabels: ["B"] },
  ]);
  assert.equal(parseRelaySegments({ type: "Turn", end_of_turn: true }), null);
});
