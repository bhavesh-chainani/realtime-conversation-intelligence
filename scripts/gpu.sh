#!/usr/bin/env bash
# Manage the AWS GPU server that runs the backend with Nemotron diarisation. Run from the laptop.
#
#   scripts/gpu.sh status    instance state, IP and, when running, the auto-stop status
#   scripts/gpu.sh start     start the instance and wait until SSH answers
#   scripts/gpu.sh deploy    copy this checkout to the server, install, restart services, check /ready
#   scripts/gpu.sh connect   tunnel the server's backend to localhost:8000 (Ctrl+C to close)
#   scripts/gpu.sh stop      stop the instance (the disk and setup are kept)
#
# Settings (instance name, region, SSH key) are in scripts/gpu_host.conf. Needs the AWS CLI.
# A stopped instance gets a new public IP on every start; this script looks it up each time.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=gpu_host.conf
source scripts/gpu_host.conf
LOCAL_PORT=${LOCAL_PORT:-8000}

aws_ec2() { aws ec2 --region "$AWS_REGION" "$@"; }

instance_field() {
  aws_ec2 describe-instances --filters "Name=tag:Name,Values=$INSTANCE_NAME" \
    "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query "Reservations[0].Instances[0].$1" --output text
}

instance_id() {
  local id
  id=$(instance_field InstanceId)
  [ "$id" != "None" ] || { echo "No EC2 instance tagged Name=$INSTANCE_NAME in $AWS_REGION" >&2; exit 1; }
  echo "$id"
}

instance_ip() {
  local ip
  ip=$(instance_field PublicIpAddress)
  [ "$ip" != "None" ] || { echo "$INSTANCE_NAME is not running (scripts/gpu.sh start)" >&2; exit 1; }
  echo "$ip"
}

ssh_opts=(-i "$SSH_KEY" -o ConnectTimeout=20 -o ServerAliveInterval=15 -o StrictHostKeyChecking=accept-new)
remote() { ssh "${ssh_opts[@]}" "$SSH_USER@$(instance_ip)" "$@"; }

wait_ready() {
  # The model takes a few seconds to load after a restart.
  for _ in $(seq 1 60); do
    if body=$(remote curl -sf localhost:8000/ready 2>/dev/null); then
      if grep -q '"diarization":{"ready":true' <<<"$body"; then
        echo "$body"
        return 0
      fi
    fi
    sleep 3
  done
  echo "Backend not ready after 3 minutes; last /ready: ${body:-none}" >&2
  echo "Logs: scripts/gpu.sh status, then ssh in and run: journalctl -u rci-backend -n 50" >&2
  return 1
}

case "${1:-}" in
  status)
    echo "$INSTANCE_NAME ($(instance_id)): $(instance_field State.Name), IP $(instance_field PublicIpAddress)"
    if [ "$(instance_field State.Name)" = running ]; then remote rci-autostop-status; fi
    ;;
  start)
    id=$(instance_id)
    aws_ec2 start-instances --instance-ids "$id" >/dev/null
    echo "Starting $INSTANCE_NAME ($id)..."
    aws_ec2 wait instance-running --instance-ids "$id"
    ip=$(instance_ip)
    for _ in $(seq 1 30); do
      ssh "${ssh_opts[@]}" -o ConnectTimeout=5 "$SSH_USER@$ip" true 2>/dev/null && break
      sleep 5
    done
    echo "Running at $ip. The idle auto-stop (scripts/gpu_host.conf) stops it when unused."
    ;;
  deploy)
    ip=$(instance_ip)
    # The server keeps its own .env, data/ (model weights, demo DB) and .venv.
    rsync -az --delete -e "ssh ${ssh_opts[*]}" \
      --exclude .git --exclude .venv --exclude realtime-venv --exclude data --exclude .env \
      --exclude frontend --exclude node_modules --exclude __pycache__ --exclude '*.egg-info' \
      --exclude .pytest_cache --exclude .ruff_cache \
      ./ "$SSH_USER@$ip:$REMOTE_DIR/"
    remote "set -e; cd $REMOTE_DIR; unset LD_LIBRARY_PATH
      [ -d .venv ] || python3 -m venv .venv
      .venv/bin/pip install -q --upgrade pip
      .venv/bin/pip install -q -e '.[dev,diarization]'
      sudo scripts/install_gpu_services.sh >/dev/null
      sudo systemctl restart rci-demo-db rci-backend"
    echo "Deployed to $ip; waiting for the backend..."
    wait_ready
    ;;
  connect)
    remote rci-autostop-status
    echo
    echo "Tunnel open: backend at http://localhost:$LOCAL_PORT (Ctrl+C to close). Then: cd frontend && npm run dev"
    exec ssh "${ssh_opts[@]}" -N -o ExitOnForwardFailure=yes -L "$LOCAL_PORT:localhost:8000" "$SSH_USER@$(instance_ip)"
    ;;
  stop)
    id=$(instance_id)
    aws_ec2 stop-instances --instance-ids "$id" >/dev/null
    echo "Stopping $INSTANCE_NAME ($id)..."
    aws_ec2 wait instance-stopped --instance-ids "$id"
    echo "Stopped."
    ;;
  *)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
