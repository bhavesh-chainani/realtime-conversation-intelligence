#!/usr/bin/env bash
# Set up a Linux machine with an NVIDIA GPU to run the backend with Nemotron 3 Diarization.
#
# Run on the GPU host from the repo root, after cloning:
#   scripts/setup_gpu_host.sh
# Then copy your .env (AssemblyAI + LLM keys) next to it and start the backend as printed at the end.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== GPU"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader \
  || { echo "nvidia-smi failed: no NVIDIA GPU or driver visible on this machine"; exit 1; }

echo "== Python environment (realtime-venv)"
PYTHON=${PYTHON:-python3}
[ -d realtime-venv ] || "$PYTHON" -m venv realtime-venv
realtime-venv/bin/pip install -q --upgrade pip
realtime-venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt -r requirements-diarization.txt
realtime-venv/bin/python -c "
import torch
assert torch.cuda.is_available(), 'torch is installed but cannot see the GPU (driver / CUDA mismatch?)'
print('torch', torch.__version__, '| CUDA', torch.version.cuda, '|', torch.cuda.get_device_name(0))
"

echo "== Model weights (data/models/Nemotron-3-Diarization)"
realtime-venv/bin/python -c "
from huggingface_hub import snapshot_download
snapshot_download('nvidia/Nemotron-3-Diarization', local_dir='data/models/Nemotron-3-Diarization',
                  allow_patterns=['config.json', 'processor_config.json', 'model.safetensors'])
"

echo "== Speed check (low_latency = 1.04 s profile)"
if [ -f data/demo_audio/katherine_liao_brightpath.wav ]; then
  realtime-venv/bin/python scripts/spike_nemotron.py --device cuda --modes low_latency,ultra_low_latency --repeat 2 \
    2>&1 | grep -E "offline|step p50"
else
  echo "(skipped: no demo WAV; generate one with scripts/make_demo_audio.py)"
fi

cat <<'EOF'

== Done. Start the backend (keep it bound to localhost; reach it over SSH):
  DIARIZATION_BACKEND=nemotron DIARIZATION_DEVICE=cuda \
    realtime-venv/bin/uvicorn backend.api:app --host 127.0.0.1 --port 8000

On the laptop running the frontend:
  ssh -N -L 8000:localhost:8000 <user>@<this-host>
  cd frontend && npm run dev     # the app talks to localhost:8000 as usual
Check: curl localhost:8000/ready  ->  "diarization": {"backend": "nemotron", "ready": true, ...}
EOF
