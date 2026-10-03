"""NVIDIA Nemotron 3 Diarization, streamed per call (torch / transformers imported lazily).

One model is shared by every call; each call gets a `NemotronSession` holding its own
speaker cache. Model steps run on a single worker thread, so calls never compete for
CPU cores mid-step and the event loop never blocks on inference.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import config as cfg

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME_MS = 10  # one speaker-probability row per 10 ms of audio


class NemotronDiarizer:
    def __init__(self, model: str, device: str, mode: str, int8: bool, threads: int):
        self.model_name, self.device, self.mode, self.int8, self.threads = model, device, mode, int8, threads
        self.ready = False
        self.error: str | None = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="diarizer")
        self._model = None
        self._processor = None

    def load(self) -> None:
        import torch
        from transformers import AutoModelForAudioFrameClassification, AutoProcessor

        started = time.perf_counter()
        if self.device == "cpu":
            torch.set_num_threads(self.threads)
        processor = AutoProcessor.from_pretrained(self.model_name)
        if self.mode not in processor.streaming_modes:
            chunk, right = (int(v) for v in self.mode.split("x"))
            processor.streaming_modes = {**processor.streaming_modes, self.mode: (chunk, right)}
        processor.set_streaming_mode(self.mode)
        model = AutoModelForAudioFrameClassification.from_pretrained(self.model_name).eval().to(self.device)
        if self.int8 and self.device == "cpu":
            model = torch.ao.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
        self._model, self._processor = model, processor
        # The first forward pass is several times slower (kernel setup): pay it now, not on a call.
        warm = NemotronSession(self, SAMPLE_RATE)
        warm.feed(b"\x00\x00" * SAMPLE_RATE * 6)
        warm.close()
        self.ready = True
        logger.info(
            "Nemotron diarizer ready: %s on %s, mode=%s (%d ms buffer), int8=%s, %.1fs",
            self.model_name, self.device, self.mode, processor.streaming_latency_ms, self.int8,
            time.perf_counter() - started,
        )

    @property
    def processor(self):
        return self._processor

    def step(self, audio, cache, first: bool, last: bool):
        """One streaming chunk (runs on the worker thread): returns (probability rows, new cache)."""
        import torch

        inputs = self._processor(
            audio, sampling_rate=SAMPLE_RATE, is_streaming=True, is_first_audio_chunk=first, is_last_audio_chunk=last
        ).to(self.device)
        with torch.inference_mode():
            out = self._model(**inputs, speaker_cache=cache)
        return out.logits[0].sigmoid().float().cpu().tolist(), out.speaker_cache

    def new_session(self, sample_rate: int) -> "NemotronSession":
        return NemotronSession(self, sample_rate)


class NemotronSession:
    """Speaker activity for one call. `feed` raw Int16 PCM as it arrives; read `probs`."""

    def __init__(self, engine: NemotronDiarizer, sample_rate: int):
        import numpy as np
        import soxr

        self._np = np
        self._engine = engine
        self._resampler = (
            soxr.ResampleStream(sample_rate, SAMPLE_RATE, 1, dtype="float32") if sample_rate != SAMPLE_RATE else None
        )
        self._lock = threading.Lock()
        self._audio = np.zeros(0, dtype=np.float32)  # 16 kHz samples from `_offset` on
        self._offset = 0
        self._mel_idx = 0
        self._first = True
        self._cache = None
        self._busy = False
        self._finished = False
        self.probs: list[list[float]] = []
        self.failed: str | None = None

    @property
    def processed_until_ms(self) -> int:
        return len(self.probs) * FRAME_MS

    def feed(self, pcm16: bytes) -> None:
        np = self._np
        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        if self._resampler is not None:
            samples = self._resampler.resample_chunk(samples)
        with self._lock:
            self._audio = np.concatenate([self._audio, samples])
            if self._busy or self.failed or self._finished:
                return
            self._busy = True
        self._engine.executor.submit(self._drain, False)

    def close(self) -> None:
        """Diarise the remaining audio (blocks until done; call off the event loop)."""
        if self._resampler is not None:
            tail = self._resampler.resample_chunk(self._np.zeros(0, dtype=self._np.float32), last=True)
            with self._lock:
                self._audio = self._np.concatenate([self._audio, tail])
        self._engine.executor.submit(self._drain, True).result()

    def _next_chunk(self, final: bool):
        p = self._engine.processor
        if self._first:
            start, size = 0, p.num_samples_first_audio_chunk
        else:
            start, size = p.audio_chunk_start(self._mel_idx), p.num_samples_per_audio_chunk
        with self._lock:
            end = self._offset + len(self._audio)
            lo = start - self._offset
            if end >= start + size:
                return self._audio[lo : lo + size].copy(), False
            # A last chunk needs at least one encoder frame of audio.
            if final and end - start > p.subsampling_factor * p.feature_extractor.hop_length:
                return self._audio[lo:].copy(), True
        return None

    def _drain(self, final: bool) -> None:
        p = self._engine.processor
        try:
            while not self._finished and (nxt := self._next_chunk(final)):
                chunk, last = nxt
                rows, self._cache = self._engine.step(chunk, self._cache, self._first, last)
                self.probs.extend(rows)
                if last:
                    self._finished = True
                    break
                self._mel_idx += p.num_mel_frames_per_step
                self._first = False
                keep_from = p.audio_chunk_start(self._mel_idx)
                with self._lock:
                    drop = keep_from - self._offset
                    if drop > 0:
                        self._audio = self._audio[drop:]
                        self._offset = keep_from
        except Exception as exc:  # the relay falls back to AssemblyAI labels for this call
            logger.exception("Diarization step failed")
            self.failed = str(exc)[:200]
        finally:
            with self._lock:
                self._busy = False


_diarizer: NemotronDiarizer | None = None


def get_diarizer() -> NemotronDiarizer | None:
    """The loaded model, or None when diarisation is off or the model failed to load."""
    return _diarizer if _diarizer is not None and _diarizer.ready else None


def load_error() -> str | None:
    return _diarizer.error if _diarizer is not None else None


def load_diarizer() -> NemotronDiarizer:
    """Load the configured model (blocking; run in a thread at startup)."""
    global _diarizer
    _diarizer = NemotronDiarizer(
        cfg.DIARIZATION_MODEL, cfg.DIARIZATION_DEVICE, cfg.DIARIZATION_MODE, cfg.DIARIZATION_INT8, cfg.DIARIZATION_THREADS
    )
    try:
        _diarizer.load()
    except Exception as exc:
        _diarizer.error = str(exc)[:300]
        logger.exception("Nemotron diarizer failed to load; using AssemblyAI speaker labels")
    return _diarizer
