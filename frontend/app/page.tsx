"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";

import { CustomerPanel } from "./components/customer-panel";
import { SessionHeader } from "./components/session-header";
import { SuggestionsPanel } from "./components/suggestions-panel";
import { TranscriptPanel } from "./components/transcript-panel";
import { useAssist } from "./hooks/useAssist.ts";
import { useCallClock } from "./hooks/useCallClock.ts";
import { useLiveTranscript } from "./hooks/useLiveTranscript.ts";
import { getBackendUrl } from "./lib/backend-url.ts";
import type { Turn } from "./lib/types.ts";

export default function Page() {
  const backendUrl = useMemo(getBackendUrl, []);
  const assist = useAssist(backendUrl);
  const clock = useCallClock();
  const { run } = assist;
  const onCustomerTurn = useCallback((turn: Turn, turns: Turn[]) => run(turn, turns), [run]);
  const transcript = useLiveTranscript(backendUrl, onCustomerTurn);

  const transcriptListRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = transcriptListRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [transcript.turns, transcript.live, transcript.pendingTurns]);

  const listen = () => {
    clock.start();
    void transcript.start();
  };
  const endCall = () => {
    transcript.close();
    assist.end();
    clock.end();
  };
  const newCall = () => {
    transcript.reset();
    assist.reset();
    clock.reset();
  };

  const roleMap = Object.entries(transcript.speakerRoleMap);
  const mappedStaffLabel = roleMap.find(([, r]) => r === "staff")?.[0];
  const mappedCustomerLabel = roleMap.find(([, r]) => r === "customer")?.[0];
  const callActive = clock.startedAt !== null && clock.endedAt === null;
  const hasTranscript = transcript.turns.some((t) => t.text.trim().length > 0);
  const citedIds = useMemo(
    () => new Set(assist.suggestions.flatMap((s) => s.linked_records || [])),
    [assist.suggestions]
  );
  const status = transcript.isListening
    ? "Listening"
    : transcript.sttDropped
      ? "Transcription disconnected · press Resume"
      : "Ready";

  return (
    <div className="shell shell--workspace">
      <SessionHeader
        isLive={transcript.isListening}
        isConnecting={transcript.isConnecting}
        callerName={assist.customer.name.trim() || null}
        startedAt={clock.startedAt}
        endedAt={clock.endedAt}
        micLevel={transcript.micLevel}
        canEndCall={callActive && transcript.turns.length > 0}
        onEndCall={endCall}
        canPause={callActive && transcript.isListening}
        canResume={callActive && !transcript.isListening}
        onPause={transcript.stop}
        onResume={listen}
        canStartNewCall={clock.endedAt !== null}
        onNewCall={newCall}
        onStart={listen}
        onStop={transcript.close}
      />

      <main className="workspace-grid">
        <TranscriptPanel
          turns={transcript.turns}
          live={transcript.live}
          pending={transcript.pendingTurns}
          speaking={transcript.speaking}
          status={status}
          hasRoleMapping={roleMap.length > 0}
          mappedStaffLabel={mappedStaffLabel}
          mappedCustomerLabel={mappedCustomerLabel}
          onSwapSpeakerRoles={transcript.swapSpeakerRoles}
          onFlipTurn={transcript.flipTurn}
          transcriptListRef={transcriptListRef}
        />

        <aside className="panel assistance-rail" aria-label="Operator assistance workspace">
          <SuggestionsPanel
            suggestions={assist.suggestions}
            meta={assist.suggestionMeta}
            cases={assist.history.cases}
            hasTranscript={hasTranscript}
            isLive={transcript.isListening}
            isFetchingSuggestions={assist.fetching}
          />

          <CustomerPanel
            customerData={assist.customer}
            fieldSources={assist.sources}
            citedIds={citedIds}
            onCustomerDataChange={assist.editField}
            onLookup={() => void assist.lookup()}
            customerHistoryStatus={assist.history.status}
            customerHistoryMessage={assist.history.summary}
            customerHistoryCases={assist.history.cases}
            historyMeta={assist.history.meta}
            isLoadingCustomerHistory={assist.history.status === "loading"}
          />
        </aside>
      </main>
    </div>
  );
}
