"use client";
import { useEffect, useMemo, useRef, useState, useCallback } from "react";

function useLocalStorage(key: string, initial: string) {
  const [value, setValue] = useState<string>(() => {
    if (typeof window === 'undefined') return initial;
    return localStorage.getItem(key) ?? initial;
  });
  useEffect(() => { try { localStorage.setItem(key, value); } catch {} }, [key, value]);
  return [value, setValue] as const;
}

type Suggestion = { 
  type?: string; 
  text?: string; 
  topic?: string; 
  confidence?: number; 
  details?: { 
    possibleConversation?: string;
    operatorResponse?: string;
    suggestedConversation?: string;
    priority?: string;
    [key: string]: any;
  }; 
};

type CustomerData = {
  name: string;
  nric_worker_permit_id: string;
  address: string;
  purpose_of_call: string;
};

type CustomerDataFields = keyof CustomerData;

export default function Page() {
  const [backendUrl] = useLocalStorage('BACKEND_URL', 'http://localhost:8000');
  const [aaiKey, setAaiKey] = useState<string>('');
  /** AssemblyAI streaming v3: boosted terms via `keyterms_prompt` query param (not legacy `word_boost`). */
  const [aaiKeyterms, setAaiKeyterms] = useState<string[]>([]);
  const [turns, setTurns] = useState<string[]>([]);
  const [live, setLive] = useState<string>('');
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [customerData, setCustomerData] = useState<CustomerData>({
    name: '',
    nric_worker_permit_id: '',
    address: '',
    purpose_of_call: ''
  });
  const [manuallyEditedFields, setManuallyEditedFields] = useState<Set<CustomerDataFields>>(new Set());
  const manuallyEditedFieldsRef = useRef<Set<CustomerDataFields>>(new Set());
  const wsRef = useRef<WebSocket | null>(null);
  const mediaRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const liveRef = useRef<string>(''); // Track live text for deduplication checks
  const lastCustomerDataExtractRef = useRef<string>(''); // Track last transcript we extracted from

  // Fetch AssemblyAI API key from backend on mount
  useEffect(() => {
    async function fetchApiKey() {
      try {
        const res = await fetch(`${backendUrl}/assemblyai-key`);
        if (res.ok) {
          const data = await res.json();
          if (data.api_key) {
            setAaiKey(data.api_key);
            if (Array.isArray(data.keyterms_prompt)) {
              setAaiKeyterms(data.keyterms_prompt.map((t: unknown) => String(t)));
            } else {
              setAaiKeyterms([]);
            }
          } else if (data.error) {
            console.error('[Frontend] Failed to load AssemblyAI API key:', data.error);
          }
        } else {
          console.error('[Frontend] Failed to fetch AssemblyAI API key:', res.status);
        }
      } catch (err) {
        console.error('[Frontend] Error fetching AssemblyAI API key:', err);
      }
    }
    fetchApiKey();
  }, [backendUrl]);

  // Keep ref in sync with state
  useEffect(() => {
    manuallyEditedFieldsRef.current = manuallyEditedFields;
  }, [manuallyEditedFields]);

  const transcriptText = useMemo(() => turns.join(' ').trim(), [turns]);

  // Keep liveRef in sync with live state
  useEffect(() => {
    liveRef.current = live;
  }, [live]);

  async function openWs() {
    if (!aaiKey) {
      alert('AssemblyAI API key not loaded. Please ensure ASSEMBLYAI_API_KEY is set in your .env file and the backend is running.');
      return;
    }
    
    const media = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRef.current = media;
    const AudioContextCls = (window as any).AudioContext || (window as any).webkitAudioContext;
    const ctx = new AudioContextCls();
    audioCtxRef.current = ctx;
    const source = ctx.createMediaStreamSource(media);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    processorRef.current = proc;
    source.connect(proc); proc.connect(ctx.destination);
    function pcmEncode(input: Float32Array) {
      const out = new Int16Array(input.length);
      for (let i=0;i<input.length;i++) out[i] = Math.max(-1, Math.min(1, input[i])) * 0x7fff;
      return out.buffer;
    }
    
    // Connect directly to AssemblyAI WebSocket v3 (keyterms_prompt = JSON array string per API docs)
    const params = new URLSearchParams({ sample_rate: String(ctx.sampleRate || 48000), format_turns: 'true', token: aaiKey });
    if (aaiKeyterms.length > 0) {
      params.set('keyterms_prompt', JSON.stringify(aaiKeyterms));
    }
    const ws = new WebSocket(`wss://streaming.assemblyai.com/v3/ws?${params}`);
    wsRef.current = ws;
    
    proc.onaudioprocess = (e: AudioProcessingEvent) => {
      if (!ws || ws.readyState !== 1) return;
      const pcm = pcmEncode(e.inputBuffer.getChannelData(0));
      try { ws.send(pcm); } catch {}
    };
    
    ws.onmessage = (evt) => {
      try {
        const d = JSON.parse(evt.data as string);
        const text: string = d.transcript || d.text || '';
        if (!text) return;
        
        // Robust final/partial detection (supports AssemblyAI message_type and type)
        const mt = String(d.message_type || '').toLowerCase();
        const tt = String(d.type || '').toLowerCase();
        const isFinal = (
          d.end_of_turn === true ||
          String(d.end_of_turn).toLowerCase() === 'true' ||
          tt.includes('final') ||
          mt.includes('final') ||
          mt === 'transcript_complete'
        );
        
        // Helper function to normalize text for duplicate comparison
        const normalizeForCompare = (s: string): string => {
          return s
            .toLowerCase()
            .replace(/[^\w\s]/g, '') // Remove punctuation
            .replace(/\s+/g, ' ')     // Normalize whitespace
            .trim();
        };
        
        // Helper function to normalize a word (convert numbers to a standard form for comparison)
        const normalizeWord = (word: string): string => {
          // Convert number words to digits for comparison
          const numberWords: { [key: string]: string } = {
            'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4',
            'five': '5', 'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
            'ten': '10', 'eleven': '11', 'twelve': '12', 'thirteen': '13',
            'fourteen': '14', 'fifteen': '15', 'sixteen': '16', 'seventeen': '17',
            'eighteen': '18', 'nineteen': '19', 'twenty': '20'
          };
          const lower = word.toLowerCase();
          if (numberWords[lower]) {
            return numberWords[lower];
          }
          // If it's already a digit, keep it as is
          if (/^\d+$/.test(word)) {
            return word;
          }
          return lower;
        };
        
        // Helper function to check if two texts are similar enough to be considered duplicates
        const areSimilar = (text1: string, text2: string): boolean => {
          if (!text1 || !text2) return false;
          
          const norm1 = normalizeForCompare(text1);
          const norm2 = normalizeForCompare(text2);
          
          // Exact match after normalization
          if (norm1 === norm2) return true;
          
          // Tokenize into words
          const words1 = norm1.split(/\s+/).filter(w => w.length > 0).map(normalizeWord);
          const words2 = norm2.split(/\s+/).filter(w => w.length > 0).map(normalizeWord);
          
          // If word counts are very different, not similar
          if (Math.abs(words1.length - words2.length) > 2) return false;
          
          // Check overlap: count how many words match (after number normalization)
          const set1 = new Set(words1);
          const set2 = new Set(words2);
          
          // Count matches
          let matches = 0;
          for (const word of set1) {
            if (set2.has(word)) matches++;
          }
          
          // Consider similar if most words match (at least 80% of unique words)
          const minWords = Math.min(set1.size, set2.size);
          if (minWords === 0) return false;
          const similarity = matches / minWords;
          
          return similarity >= 0.8;
        };
        
        // REAL-TIME DISPLAY
        if (isFinal) {
          const trimmed = text.trim();
          if (!trimmed) return;
          const normalizedNew = normalizeForCompare(trimmed);
          const normalizedLive = normalizeForCompare(liveRef.current || '');
          
          setTurns(prev => {
            if (prev.length === 0) {
              return [trimmed];
            }
            const last = prev[prev.length - 1];
            const normalizedLast = normalizeForCompare(last);
            
            // If final matches current live or last final (normalized or similar), replace last turn
            if (normalizedNew === normalizedLive || normalizedNew === normalizedLast || areSimilar(trimmed, liveRef.current || '') || areSimilar(trimmed, last)) {
              return [...prev.slice(0, -1), trimmed];
            }
            
            // Otherwise append as a new turn
            return [...prev, trimmed];
          });
          
          // Clear live
          setLive('');
          liveRef.current = '';
        } else {
          // Always show partial immediately (no gating)
          const trimmed = text.trim();
          setLive(trimmed);
          liveRef.current = trimmed;
        }
      } catch (err) {
        console.error('[Frontend] WebSocket message error:', err);
      }
    };
  }

  function closeWs() {
    try { wsRef.current?.close(); } catch {}
    wsRef.current = null;
    try { processorRef.current?.disconnect(); } catch {}
    processorRef.current = null;
    try { audioCtxRef.current?.close(); } catch {}
    audioCtxRef.current = null;
    try { mediaRef.current?.getTracks().forEach(t => t.stop()); } catch {}
    mediaRef.current = null;
    setLive('');
    liveRef.current = '';
  }

  // Handler for manual customer data field changes
  const handleCustomerDataChange = (field: CustomerDataFields, value: string) => {
    setCustomerData(prev => ({ ...prev, [field]: value }));
    setManuallyEditedFields(prev => new Set(prev).add(field));
  };

  // Extract customer data from transcript
  const extractCustomerData = useCallback(async (context: string) => {
    if (!context || context.trim().length < 10) {
      return;
    }
    
    // Prevent redundant extractions
    const normalizedCurrent = context.trim().toLowerCase();
    const normalizedLast = lastCustomerDataExtractRef.current.trim().toLowerCase();
    
    if (normalizedCurrent === normalizedLast) {
      return;
    }
    
    try {
      lastCustomerDataExtractRef.current = context;
      
      const res = await fetch(`${backendUrl}/extract-customer-data`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_transcript: context })
      });
      
      if (res.ok) {
        const data = await res.json();
        if (data.success && data.data) {
          // Only update fields that haven't been manually edited
          // Use ref to get the latest manuallyEditedFields value
          setCustomerData(prev => {
            const updated = { ...prev };
            const currentEditedFields = manuallyEditedFieldsRef.current;
            
            Object.keys(data.data).forEach((key) => {
              const field = key as CustomerDataFields;
              const extractedValue = data.data[field];
              
              // Only auto-fill if field hasn't been manually edited and has extracted value
              if (!currentEditedFields.has(field) && extractedValue) {
                updated[field] = extractedValue;
              }
            });
            
            return updated;
          });
        }
      }
    } catch (err) {
      console.error('[Frontend] Failed to extract customer data:', err);
    }
  }, [backendUrl]);

  // Ref to track the last transcript we sent to avoid redundant requests
  const lastTranscriptRef = useRef<string>('');
  // Ref to track the last time we fetched suggestions
  const lastFetchTimeRef = useRef<number>(0);
  // Ref to store the debounce timeout
  const debounceTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  
  const fetchSuggestions = async (context: string) => {
    if (!context || context.trim().length < 10) {
      setSuggestions([]);
      return;
    }
    
    // Prevent redundant fetches if transcript hasn't changed
    const normalizedCurrent = context.trim().toLowerCase();
    const normalizedLast = lastTranscriptRef.current.trim().toLowerCase();
    
    if (normalizedCurrent === normalizedLast) {
      console.log('[Frontend] Transcript unchanged, skipping suggestion fetch');
      return;
    }
    
    try {
      console.log(`[Frontend] Fetching suggestions for transcript (${context.length} chars): "${context.substring(0, 100)}..."`);
      lastTranscriptRef.current = context;
      lastFetchTimeRef.current = Date.now();
      
      const res = await fetch(`${backendUrl}/suggest`, { 
        method: 'POST', 
        headers: { 'Content-Type': 'application/json' }, 
        body: JSON.stringify({ context: context, max_suggestions: 2 }) 
      });
      if (res.ok) {
        const data = await res.json();
        console.log(`[Frontend] Received ${data.suggestions?.length || 0} suggestions`);
        setSuggestions(data.suggestions || []);
      } else {
        console.error(`[Frontend] Suggestion request failed: ${res.status}`);
      }
    } catch (err) {
      console.error('[Frontend] Failed to fetch suggestions:', err);
      // Don't clear suggestions on error - keep last ones
    }
  };

  useEffect(() => {
    // Only request suggestions and extract customer data if there's meaningful transcript content
    if (!transcriptText || transcriptText.trim().length < 10) {
      setSuggestions([]);
      lastTranscriptRef.current = '';
      lastCustomerDataExtractRef.current = '';
      // Clear any pending debounce
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current);
        debounceTimeoutRef.current = null;
      }
      return;
    }
    
    // Clear any existing debounce timeout
    if (debounceTimeoutRef.current) {
      clearTimeout(debounceTimeoutRef.current);
    }
    
    // Debounce: Wait 1.5 seconds after transcript stops changing before fetching
    // This ensures we only fetch when the user has finished speaking (final turns have stabilized)
    debounceTimeoutRef.current = setTimeout(() => {
      // Check again if transcript has changed during the debounce period
      const normalizedCurrent = transcriptText.trim().toLowerCase();
      const normalizedLast = lastTranscriptRef.current.trim().toLowerCase();
      
      // Only fetch if transcript has actually changed
      if (normalizedCurrent !== normalizedLast) {
        console.log('[Frontend] Transcript stabilized, fetching suggestions and extracting customer data...');
        fetchSuggestions(transcriptText);
        extractCustomerData(transcriptText);
      }
      debounceTimeoutRef.current = null;
    }, 1500); // 1.5 second debounce - waits for conversation to stabilize
    
    return () => {
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current);
        debounceTimeoutRef.current = null;
      }
    };
  }, [backendUrl, transcriptText, extractCustomerData]);

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', margin: 10 }}>
      <header style={{ background: 'rgba(255,255,255,.9)', borderRadius: 12, padding: '12px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', fontWeight: 700, color: '#4a5568' }}>Real Time Conversation Intelligence</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button onClick={()=>openWs()} style={{ padding: '6px 10px', borderRadius: 8, border: 'none', background: '#667eea', color: 'white' }}>Start</button>
          <button onClick={()=>{ setLive(''); closeWs(); }} style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid #ddd', background: 'white' }}>Stop</button>
        </div>
      </header>
      <main style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 800px', gap: 20, marginTop: 12 }}>
        <section style={{ background: 'rgba(255,255,255,.95)', borderRadius: 12, padding: 16, overflow: 'auto' }}>
          <h2 style={{ margin: 0, marginBottom: 8, color: '#2d3748' }}>Live Conversation</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {turns.map((t, i) => (
              <div key={i} style={{ background: 'rgba(102,126,234,.1)', border: '1px solid rgba(102,126,234,.2)', color: '#2d3748', padding: '12px 16px', borderRadius: 18, maxWidth: '70%' }}>{t}</div>
            ))}
            {live && (
              <div style={{ background: 'rgba(102,126,234,.1)', border: '1px solid rgba(102,126,234,.2)', color: '#2d3748', padding: '12px 16px', borderRadius: 18, maxWidth: '70%' }}><em>{live}</em></div>
            )}
          </div>
        </section>
        <aside style={{ background: 'rgba(255,255,255,.95)', borderRadius: 12, padding: 16, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 24 }}>
          {/* Customer Data Section */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxHeight: '35vh', overflowY: 'auto' }}>
            <div>
              <h2 style={{ margin: 0, marginBottom: 8, color: '#2d3748', fontSize: 18, fontWeight: 600 }}>Customer Data</h2>
              <div style={{ fontSize: 13, color: '#718096', marginBottom: 16 }}>
                Customer information extracted from conversation. You can manually edit any field.
              </div>
            </div>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {/* Name Field */}
              <div>
                <label style={{ display: 'block', fontSize: 12, color: '#718096', fontWeight: 600, marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Name
                </label>
                <input
                  type="text"
                  value={customerData.name}
                  onChange={(e) => handleCustomerDataChange('name', e.target.value)}
                  placeholder="Customer name"
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    border: '1px solid #e2e8f0',
                    borderRadius: 8,
                    fontSize: 14,
                    color: '#2d3748',
                    background: 'white',
                    boxSizing: 'border-box'
                  }}
                />
              </div>

              {/* NRIC/Worker's Permit ID Field */}
              <div>
                <label style={{ display: 'block', fontSize: 12, color: '#718096', fontWeight: 600, marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  NRIC/Worker's Permit ID
                </label>
                <input
                  type="text"
                  value={customerData.nric_worker_permit_id}
                  onChange={(e) => handleCustomerDataChange('nric_worker_permit_id', e.target.value)}
                  placeholder="S1234567A, T1234567A, or Work Permit ID"
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    border: '1px solid #e2e8f0',
                    borderRadius: 8,
                    fontSize: 14,
                    color: '#2d3748',
                    background: 'white',
                    boxSizing: 'border-box'
                  }}
                />
              </div>

              {/* Address Field */}
              <div>
                <label style={{ display: 'block', fontSize: 12, color: '#718096', fontWeight: 600, marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Address
                </label>
                <textarea
                  value={customerData.address}
                  onChange={(e) => handleCustomerDataChange('address', e.target.value)}
                  placeholder="Customer address"
                  rows={3}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    border: '1px solid #e2e8f0',
                    borderRadius: 8,
                    fontSize: 14,
                    color: '#2d3748',
                    background: 'white',
                    boxSizing: 'border-box',
                    resize: 'vertical',
                    fontFamily: 'inherit'
                  }}
                />
              </div>

              {/* Purpose of Call Field */}
              <div>
                <label style={{ display: 'block', fontSize: 12, color: '#718096', fontWeight: 600, marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Purpose of Call
                </label>
                <textarea
                  value={customerData.purpose_of_call}
                  onChange={(e) => handleCustomerDataChange('purpose_of_call', e.target.value)}
                  placeholder="Reason for the call"
                  rows={3}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    border: '1px solid #e2e8f0',
                    borderRadius: 8,
                    fontSize: 14,
                    color: '#2d3748',
                    background: 'white',
                    boxSizing: 'border-box',
                    resize: 'vertical',
                    fontFamily: 'inherit'
                  }}
                />
              </div>
            </div>

            {transcriptText.trim().length < 10 && (
              <div style={{ color: '#718096', fontSize: 13, fontStyle: 'italic', textAlign: 'center', padding: 20, background: '#f7fafc', borderRadius: 8 }}>
                Customer data will be automatically extracted as the conversation progresses...
              </div>
            )}
          </div>

          {/* Divider */}
          <div style={{ borderTop: '2px solid #e2e8f0', margin: '8px 0' }}></div>

          {/* AI Suggestions Section */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1, minHeight: '50vh' }}>
            <h2 style={{ margin: 0, marginBottom: 8, color: '#2d3748', fontSize: 18, fontWeight: 600 }}>AI Suggestions</h2>
            {suggestions.length === 0 ? (
              <div style={{ color: '#718096', fontSize: 16, fontStyle: 'italic', textAlign: 'center', padding: 20 }}>
                {transcriptText.trim().length < 10 
                  ? 'AI suggestions will appear here as the conversation progresses...' 
                  : 'Analyzing conversation...'}
              </div>
            ) : (
              suggestions.map((s, i) => {
                const details = s.details || {};
                const topic = s.topic || s.text || 'Follow up on conversation';
                const possibleConversation = details.possibleConversation || '';
                
                return (
                  <div key={i} style={{ background: 'white', border: '1px solid #eee', borderRadius: 12, padding: 12 }}>
                    <div style={{ fontSize: 14, color: '#667eea', fontWeight: 600, marginBottom: 8 }}>{s.type || 'Suggestion'}</div>
                    
                    {/* Topic/Context to follow up on */}
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: 12, color: '#718096', fontWeight: 600, marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                        Topic/Context:
                      </div>
                      <div style={{ fontSize: 16, color: '#2d3748', background: '#f7fafc', padding: 10, borderRadius: 8, borderLeft: '3px solid #667eea' }}>
                        {topic}
                      </div>
                    </div>
                    
                    {/* Possible conversation for operator */}
                    {possibleConversation && (
                      <div>
                        <div style={{ fontSize: 12, color: '#718096', fontWeight: 600, marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                          Possible conversation:
                        </div>
                        <div style={{ fontSize: 16, color: '#4a5568', fontStyle: 'italic', lineHeight: 1.6 }}>
                          {possibleConversation}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </aside>
      </main>
    </div>
  );
}


