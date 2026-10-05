// Microphone capture: Int16 PCM chunks for the STT relay, plus a level meter for the header.

export type MicCapture = { sampleRate: number; stop: () => void };

function pcmEncode(input: Float32Array): ArrayBuffer {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i += 1) {
    out[i] = Math.max(-1, Math.min(1, input[i])) * 0x7fff;
  }
  return out.buffer;
}

export async function startMicCapture(
  onPcm: (pcm: ArrayBuffer) => void,
  onLevel: (level: number) => void
): Promise<MicCapture> {
  const media = await navigator.mediaDevices.getUserMedia({ audio: true });
  const AudioContextCls = window.AudioContext || (window as any).webkitAudioContext;
  const ctx: AudioContext = new AudioContextCls();
  const source = ctx.createMediaStreamSource(media);
  // ScriptProcessorNode is deprecated but needs no separate worklet file; 4096 frames ≈ 85 ms at 48 kHz.
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  source.connect(proc);
  proc.connect(ctx.destination);
  proc.onaudioprocess = (e) => onPcm(pcmEncode(e.inputBuffer.getChannelData(0)));

  // Mic level at ~10 Hz.
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);
  const samples = new Uint8Array(analyser.fftSize);
  const timer = setInterval(() => {
    analyser.getByteTimeDomainData(samples);
    let sum = 0;
    for (const v of samples) {
      const x = (v - 128) / 128;
      sum += x * x;
    }
    onLevel(Math.min(1, Math.sqrt(sum / samples.length) * 5));
  }, 100);

  return {
    sampleRate: ctx.sampleRate || 48000,
    stop: () => {
      clearInterval(timer);
      proc.onaudioprocess = null;
      for (const node of [proc, source, analyser]) {
        try {
          node.disconnect();
        } catch {}
      }
      void ctx.close().catch(() => {});
      media.getTracks().forEach((track) => track.stop());
      onLevel(0);
    },
  };
}
