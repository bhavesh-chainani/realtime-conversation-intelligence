"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";

import { CustomerPanel } from "./components/customer-panel";
import { SessionHeader } from "./components/session-header";
import { SuggestionsPanel } from "./components/suggestions-panel";
import { TranscriptPanel } from "./components/transcript-panel";
import { WrapUpPanel } from "./components/wrapup-panel";
import { useAssist } from "./hooks/useAssist.ts";
import { useCallClock } from "./hooks/useCallClock.ts";
import { useLiveTranscript } from "./hooks/useLiveTranscript.ts";
import { useWrapUp } from "./hooks/useWrapUp.ts";
import { getBackendUrl } from "./lib/backend-url.ts";

export default function Page() {
  const backendUrl = useMemo(getBackendUrl, []);
  const assist = useAssist(backendUrl);
  const clock = useCallClock();
  const wrapUp = useWrapUp(backendUrl);
  const transcript = useLiveTranscript(backendUrl, {
    onCustomerTurn: assist.run,
    onPendingCustomerTurn: assist.startEarly,
    onNoCustomerTurn: assist.dropEarly,
  });

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
    void wrapUp.draft({
      turns: transcript.turns,
      customer: assist.customer,
      history: assist.history,
    });
  };
  const newCall = () => {
    transcript.reset();
    assist.reset();
    wrapUp.reset();
    clock.reset();
  };
  // Stable callbacks, so the memoised panels skip the re-render on every mic-level update.
  const { lookup, customer } = assist;
  const onLookup = useCallback(() => void lookup(), [lookup]);
  const { save } = wrapUp;
  const onSave = useCallback(async () => {
    const result = await save(customer);
    // Show the saved case on the caller card, as the next call will see it.
    if (result?.status === "saved") void lookup();
  }, [save, customer, lookup]);

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
    <div className="shell">
      <SessionHeader
        isLive={transcript.isListening}
        isConnecting={transcript.isConnecting}
        callerName={assist.customer.name.trim() || null}
        startedAt={clock.startedAt}
        endedAt={clock.endedAt}
        micLevel={transcript.micLevel}
        canEndCall={callActive && transcript.turns.length > 0}
        onEndCall={endCall}
        onPause={transcript.stop}
        canResume={callActive && !transcript.isListening && !transcript.isConnecting}
        onResume={listen}
        canStartNewCall={clock.endedAt !== null}
        onNewCall={newCall}
        onStart={listen}
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
          {wrapUp.status !== "idle" ? (
            <WrapUpPanel
              status={wrapUp.status}
              wrapup={wrapUp.wrapup}
              message={wrapUp.message}
              saving={wrapUp.saving}
              saved={wrapUp.saved}
              onEdit={wrapUp.edit}
              onRetry={wrapUp.retry}
              onSave={() => void onSave()}
            />
          ) : (
            <SuggestionsPanel
              suggestions={assist.suggestions}
              meta={assist.suggestionMeta}
              cases={assist.history.cases}
              hasTranscript={hasTranscript}
              isLive={transcript.isListening}
              isFetchingSuggestions={assist.fetching}
            />
          )}

          <CustomerPanel
            customerData={assist.customer}
            fieldSources={assist.sources}
            citedIds={citedIds}
            onCustomerDataChange={assist.editField}
            onLookup={onLookup}
            customerHistoryStatus={assist.history.status}
            customerHistoryMessage={assist.history.summary}
            customerHistoryCases={assist.history.cases}
            openCount={assist.history.openCount}
          />
        </aside>
      </main>
    </div>
  );
}
