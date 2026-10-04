#!/usr/bin/env bash
# Run the backend on the GPU host as systemd services, with automatic stops so a forgotten
# instance does not keep billing. Run after scripts/setup_gpu_host.sh, from the repo root:
#   sudo scripts/install_gpu_services.sh
#
# Installs:
#   rci-demo-db       embedded Postgres with the demo customer history (scripts/demo_db.py)
#   rci-backend       uvicorn on 127.0.0.1:8000 (reach it over an SSH tunnel)
#   rci-idle-stop     every 5 min: power off after IDLE_MINUTES (default 60) with no backend
#                     connection and no interactive login; an open SSH tunnel alone does not count
#   rci-nightly-stop  power off at 02:00 Singapore time
# On EC2, powering off from inside stops the instance (disk kept; Start it again from the console).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
REPO=$(cd "$(dirname "$0")/.." && pwd)
RUN_AS=${SUDO_USER:-ubuntu}
IDLE_MINUTES=${IDLE_MINUTES:-60}

cat > /etc/systemd/system/rci-demo-db.service <<EOF
[Unit]
Description=Demo customer-history Postgres
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=$RUN_AS
WorkingDirectory=$REPO
ExecStart=$REPO/realtime-venv/bin/python scripts/demo_db.py

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/rci-backend.service <<EOF
[Unit]
Description=Realtime conversation intelligence backend (Nemotron diarisation)
After=network-online.target rci-demo-db.service
Wants=network-online.target rci-demo-db.service

[Service]
User=$RUN_AS
WorkingDirectory=$REPO
# The PyTorch wheel bundles its own CUDA/cuDNN; the AMI's system copies must not shadow them.
Environment=LD_LIBRARY_PATH=
ExecStart=$REPO/realtime-venv/bin/uvicorn backend.api:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/bin/rci-idle-stop <<EOF
#!/usr/bin/env bash
# Power off after $IDLE_MINUTES minutes without app use. The clock lives in /run, so it restarts at boot.
STATE=/run/rci-idle-last-active
now=\$(date +%s)
if ss -Htn state established '( sport = :8000 )' | grep -q . || who | grep -q . || [ ! -f "\$STATE" ]; then
  echo "\$now" > "\$STATE"
  exit 0
fi
if (( now - \$(cat "\$STATE") >= $IDLE_MINUTES * 60 )); then
  logger -t rci-idle-stop "no app use for $IDLE_MINUTES minutes: powering off"
  systemctl poweroff
fi
EOF
chmod 755 /usr/local/bin/rci-idle-stop

cat > /etc/systemd/system/rci-idle-stop.service <<'EOF'
[Unit]
Description=Power off when the backend has been idle

[Service]
Type=oneshot
ExecStart=/usr/local/bin/rci-idle-stop
EOF

cat > /etc/systemd/system/rci-idle-stop.timer <<'EOF'
[Unit]
Description=Check for idleness every 5 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/rci-nightly-stop.service <<'EOF'
[Unit]
Description=Nightly power off

[Service]
Type=oneshot
ExecStart=/usr/bin/logger -t rci-nightly-stop "nightly stop"
ExecStart=/usr/bin/systemctl poweroff
EOF

cat > /etc/systemd/system/rci-nightly-stop.timer <<'EOF'
[Unit]
Description=Power off at 02:00 Singapore time

[Timer]
OnCalendar=*-*-* 02:00:00 Asia/Singapore

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now rci-demo-db.service rci-backend.service rci-idle-stop.timer rci-nightly-stop.timer
systemctl --no-pager --lines=0 status rci-demo-db rci-backend | grep -E "●|Active:"
systemctl list-timers --no-pager | grep -E "rci-|NEXT"
