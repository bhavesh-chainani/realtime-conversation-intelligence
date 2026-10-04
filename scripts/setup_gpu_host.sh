#!/usr/bin/env bash
# Set up a Linux machine with an NVIDIA GPU to run the backend with Nemotron 3 Diarization.
#
# Run once on the GPU host from the repo root (scripts/gpu.sh deploy copies the code there):
#   scripts/setup_gpu_host.sh
# Then add a .env (AssemblyAI + LLM keys, DIARIZATION_DEVICE=cuda) and run sudo scripts/install_gpu_services.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
# Deep Learning AMIs put the system CUDA libraries on LD_LIBRARY_PATH; they clash with the cuDNN
# bundled in the PyTorch wheel (CUDNN_STATUS_SUBLIBRARY_LOADING_FAILED). The wheel needs neither.
unset LD_LIBRARY_PATH

echo "== GPU"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader \
  || { echo "nvidia-smi failed: no NVIDIA GPU or driver visible on this machine"; exit 1; }

echo "== Python environment (.venv)"
PYTHON=${PYTHON:-python3}
[ -d .venv ] || "$PYTHON" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev,diarization]"
.venv/bin/python -c "
import torch
assert torch.cuda.is_available(), 'torch is installed but cannot see the GPU (driver / CUDA mismatch?)'
print('torch', torch.__version__, '| CUDA', torch.version.cuda, '|', torch.cuda.get_device_name(0))
"

echo "== Model weights (data/models/Nemotron-3-Diarization)"
.venv/bin/python -c "
from huggingface_hub import snapshot_download
snapshot_download('nvidia/Nemotron-3-Diarization', local_dir='data/models/Nemotron-3-Diarization',
                  allow_patterns=['config.json', 'processor_config.json', 'model.safetensors'])
"

cat <<'EOF'

== Done. Run the backend and the demo DB as services (start on boot, auto-stop when idle):
  sudo scripts/install_gpu_services.sh

On the laptop running the frontend:
  scripts/gpu.sh connect         # SSH tunnel to localhost:8000
  cd frontend && npm run dev     # the app talks to localhost:8000 as usual
Check: curl localhost:8000/ready  ->  "diarization": {"ready": true, "mode": "low_latency"}
EOF
