#!/usr/bin/env bash
# Set up a Linux machine with an NVIDIA GPU to run the backend with Nemotron 3 Diarization.
#
# Run on the GPU host from the repo root, after cloning:
#   scripts/setup_gpu_host.sh
# Then copy your .env (AssemblyAI + LLM keys) next to it and start the backend as printed at the end.
set -euo pipefail
cd "$(dirname "$0")/.."
# Deep Learning AMIs put the system CUDA libraries on LD_LIBRARY_PATH; they clash with the cuDNN
# bundled in the PyTorch wheel (CUDNN_STATUS_SUBLIBRARY_LOADING_FAILED). The wheel needs neither.
unset LD_LIBRARY_PATH

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

cat <<'EOF'

== Done. Start the backend (keep it bound to localhost; reach it over SSH):
  DIARIZATION_DEVICE=cuda \
    realtime-venv/bin/uvicorn backend.api:app --host 127.0.0.1 --port 8000

On the laptop running the frontend:
  ssh -N -L 8000:localhost:8000 <user>@<this-host>
  cd frontend && npm run dev     # the app talks to localhost:8000 as usual
Check: curl localhost:8000/ready  ->  "diarization": {"backend": "nemotron", "ready": true, ...}
EOF
