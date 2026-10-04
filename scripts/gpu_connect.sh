#!/usr/bin/env bash
# Connect this laptop to the backend on the GPU server: show when the server will stop
# automatically, then tunnel it to localhost so the frontend works unchanged.
#
# Usage: scripts/gpu_connect.sh <public-ip> [local-port, default 8000]
# Stop your local backend first if it uses the same port. Ctrl+C closes the tunnel.
set -euo pipefail
IP=${1:?usage: scripts/gpu_connect.sh <public-ip> [local-port]}
PORT=${2:-8000}
KEY=${GPU_KEY:-$HOME/.ssh/rci-gpu.pem}
SSH=(ssh -i "$KEY" -o ConnectTimeout=20 -o ServerAliveInterval=15 -o StrictHostKeyChecking=accept-new "ubuntu@$IP")

"${SSH[@]}" rci-autostop-status
echo
echo "Tunnel open: backend at http://localhost:$PORT (Ctrl+C to close). Then: cd frontend && npm run dev"
exec "${SSH[@]}" -N -o ExitOnForwardFailure=yes -L "$PORT:localhost:8000"
