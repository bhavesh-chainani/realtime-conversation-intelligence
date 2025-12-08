# assembly_v3_ws_proxy.py
from fastapi import APIRouter, WebSocket, Query
import os, asyncio, base64, json, websockets
from websockets.exceptions import ConnectionClosed
from .router_agent import RouterAgent
from .suggestion_agent import SuggestionAgent
import time
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Adjust params as needed. Many laptop mics run at 48000 Hz; match that to avoid resample issues.
AAI_URL = "wss://streaming.assemblyai.com/v3/ws?sample_rate=48000&format_turns=true"

# Initialize agents
router_agent = RouterAgent()
suggestion_agent = SuggestionAgent()

@router.websocket("/ws/stt")
async def websocket_stt_proxy(ws: WebSocket, token: str = Query(None)):
    await ws.accept()

    # Get API key from query parameter (frontend) or environment variable (fallback)
    api_key = None
    if token:
        api_key = token.strip()
    if not api_key:
        api_key = os.getenv("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        await ws.close(code=4003, reason="Missing ASSEMBLYAI_API_KEY")
        return

    try:
        async with websockets.connect(
            AAI_URL,
            extra_headers=(("Authorization", api_key),),  # or put &token=... in URL
            max_size=None,
            ping_interval=10,
            ping_timeout=20,
        ) as aai_ws:

            # Expect a "session begins" JSON from AAI first; forward it to client
            try:
                begin_msg = await aai_ws.recv()
                await ws.send_text(begin_msg)
                # Proactively send config to AAI to ensure correct decoding
                try:
                    await aai_ws.send(json.dumps({
                        "config": {
                            "sample_rate": 16000,
                            "language_code": "en",
                            "punctuate": True,
                            "format_text": True
                        }
                    }))
                except Exception:
                    pass
            except Exception as e:
                await ws.close(code=4004, reason=f"AAI handshake failed: {e}")
                return

            async def client_to_aai():
                """
                - If the browser sends raw PCM bytes: base64-encode and send as a TEXT frame (string).
                - If the browser sends JSON strings for config/force-endpoint/terminate: pass through.
                """
                try:
                    frame_count = 0
                    while True:
                        try:
                            pcm = await ws.receive_bytes()
                            # Send raw PCM bytes as BINARY frames to AssemblyAI (recommended path)
                            await aai_ws.send(pcm)
                            frame_count += 1
                            if frame_count % 50 == 0:
                                try:
                                    print(f"[AAI] forwarded audio frames: {frame_count}")
                                    await ws.send_text(json.dumps({"type": "Debug", "frames_forwarded": frame_count}))
                                except Exception:
                                    pass
                        except Exception:
                            # If it's not bytes, try to receive text (JSON configs or control)
                            try:
                                txt = await ws.receive_text()
                                # If this is control/config JSON, forward it
                                try:
                                    obj = json.loads(txt)
                                    await aai_ws.send(json.dumps(obj))
                                except json.JSONDecodeError:
                                    pass
                            except Exception:
                                break  # Close on any error or client disconnect
                except ConnectionClosed:
                    pass
                except Exception:
                    # Best-effort graceful termination
                    try:
                        await aai_ws.send(json.dumps({"type": "terminate_session"}))
                    except Exception:
                        pass
                finally:
                    try:
                        await aai_ws.send(json.dumps({"type": "terminate_session"}))
                    except Exception:
                        pass

            # Conversation state tracking
            conversation_turns = []  # Finalized transcript turns
            current_partial = ""  # Current partial transcript
            last_suggestion_time = 0.0
            min_time_between_suggestions = 1.5  # Minimum seconds between suggestion requests
            
            async def process_suggestions(conversation_text: str):
                """Process suggestions asynchronously - doesn't block transcription."""
                nonlocal last_suggestion_time
                current_time = time.time()
                
                # Throttle suggestion requests
                if current_time - last_suggestion_time < min_time_between_suggestions:
                    return
                
                try:
                    # Router agent decides if we need suggestions
                    router_decision = await router_agent.should_get_suggestions(conversation_text, last_suggestion_time)
                    
                    if router_decision["should_suggest"]:
                        last_suggestion_time = current_time
                        logger.info(f"[Router] Decision: {router_decision['reason']} (confidence: {router_decision['confidence']:.2f})")
                        
                        # Get suggestions from suggestion agent
                        suggestions = await suggestion_agent.generate_suggestions(conversation_text)
                        
                        if suggestions:
                            # Send suggestions to client via WebSocket
                            suggestion_msg = {
                                "type": "Suggestions",
                                "suggestions": suggestions
                            }
                            try:
                                await ws.send_text(json.dumps(suggestion_msg))
                                logger.info(f"[Suggestions] Sent {len(suggestions)} suggestions to client")
                            except Exception as e:
                                logger.error(f"[Suggestions] Failed to send: {e}")
                except Exception as e:
                    logger.error(f"[Suggestions] Error processing: {e}")
            
            async def aai_to_client():
                """Forward AAI messages (JSON strings) to the browser, process transcripts, and generate suggestions in real-time."""
                try:
                    async for msg in aai_ws:
                        # AAI sends JSON strings; try to parse and normalize for the frontend
                        try:
                            obj = json.loads(msg)
                        except Exception:
                            obj = None

                        if isinstance(obj, dict):
                            # AssemblyAI v3 uses "message_type" field
                            msg_type = str(obj.get("message_type") or obj.get("type", "")).lower()
                            text = obj.get("text") or obj.get("transcript") or ""
                            
                            # Check if this is a transcript message
                            is_transcript = "transcript" in msg_type or "partial" in msg_type or "final" in msg_type
                            
                            # Determine if final based on message_type or end_of_turn
                            is_final = (
                                obj.get("end_of_turn") is True or 
                                "final" in msg_type or 
                                msg_type == "transcript_complete"
                            )
                            
                            if text and is_transcript:
                                # Update conversation state
                                if is_final:
                                    # Finalized turn - add to conversation history
                                    conversation_turns.append(text.strip())
                                    current_partial = ""
                                    logger.info(f"[AAI FINAL] {text}")
                                else:
                                    # Partial transcript
                                    current_partial = text.strip()
                                    logger.debug(f"[AAI PARTIAL] {text}")
                                
                                # Build full conversation context
                                full_conversation = " ".join(conversation_turns)
                                if current_partial and not is_final:
                                    full_conversation = (full_conversation + " " + current_partial).strip()
                                
                                # Send normalized message expected by UI (non-blocking)
                                normalized = {
                                    "type": "Final" if is_final else "Partial",
                                    "transcript": text,
                                    "end_of_turn": is_final
                                }
                                await ws.send_text(json.dumps(normalized))
                                logger.debug(f"[Backend] Sent transcript to client: type={normalized['type']}, text={text[:50]}...")
                                
                                # Process suggestions asynchronously (fire and forget for low latency)
                                if full_conversation and len(full_conversation.strip()) >= 20:
                                    # Only process on final turns or significant partial updates
                                    if is_final or len(current_partial) > 50:
                                        asyncio.create_task(process_suggestions(full_conversation))
                                continue

                            # Forward session begins and other control messages transparently
                            # Log for debugging
                            if msg_type:
                                logger.debug(f"[Backend] Forwarding control message: type={msg_type}")
                            await ws.send_text(json.dumps(obj))
                        else:
                            # Not JSON; forward raw
                            await ws.send_text(msg)
                except ConnectionClosed:
                    pass
                except Exception as e:
                    try:
                        await ws.close(code=4005, reason=f"AAI stream error: {e}")
                    except Exception:
                        pass

            await asyncio.gather(client_to_aai(), aai_to_client())

    except Exception as e:
        try:
            await ws.close(code=4002, reason=f"Upstream connect error: {e}")
        except Exception:
            pass
