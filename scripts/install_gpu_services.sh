#!/usr/bin/env bash
# Run the backend on the GPU host as systemd services, with automatic stops so a forgotten
# instance does not keep billing. Run after scripts/setup_gpu_host.sh, from the repo root:
#   sudo scripts/install_gpu_services.sh
# Safe to re-run (e.g. after editing scripts/gpu_host.conf).
#
# Installs:
#   rci-demo-db       embedded Postgres with the demo customer history (scripts/demo_db.py)
#   rci-backend       uvicorn on 127.0.0.1:8000 (reach it over an SSH tunnel)
#   rci-idle-stop     every 5 min: power off after IDLE_MINUTES without app use
#   rci-nightly-stop  power off at NIGHTLY_STOP (NIGHTLY_TZ)
#   rci-autostop-status  command showing the settings, idle time, next stops and past stops
# Timings live in scripts/gpu_host.conf. Every automatic stop is logged to /var/log/rci-autostop.log.
# On EC2, powering off from inside stops the instance (disk kept; Start it again from the console).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
REPO=$(cd "$(dirname "$0")/.." && pwd)
CONF=$REPO/scripts/gpu_host.conf
RUN_AS=${SUDO_USER:-ubuntu}
# shellcheck source=gpu_host.conf
source "$CONF"
LOG=/var/log/rci-autostop.log
touch "$LOG" && chmod 644 "$LOG"

# systemd-logind deletes a normal user's shared memory when their last login session ends (RemoveIPC),
# which kills the demo Postgres running as $RUN_AS as soon as an SSH session closes ("could not open
# shared memory segment"). The PostgreSQL docs' fix: keep IPC objects.
mkdir -p /etc/systemd/logind.conf.d
if ! grep -qs '^RemoveIPC=no' /etc/systemd/logind.conf.d/rci-keep-ipc.conf; then
  printf '[Login]\nRemoveIPC=no\n' > /etc/systemd/logind.conf.d/rci-keep-ipc.conf
  systemctl restart systemd-logind
fi

cat > /etc/systemd/system/rci-demo-db.service <<EOF
[Unit]
Description=Demo customer-history Postgres
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=$RUN_AS
WorkingDirectory=$REPO
ExecStart=$REPO/.venv/bin/python scripts/demo_db.py

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
ExecStart=$REPO/.venv/bin/uvicorn backend.api:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/bin/rci-idle-stop <<EOF
#!/usr/bin/env bash
# Power off after IDLE_MINUTES (from $CONF) without app use. The idle clock lives in /run, so it
# restarts at every boot.
source "$CONF"
STATE=/run/rci-idle-last-active
now=\$(date +%s)
if ss -Htn state established '( sport = :8000 )' | grep -q . || who | grep -q . || [ ! -f "\$STATE" ]; then
  echo "\$now" > "\$STATE"
  exit 0
fi
[ "\${IDLE_MINUTES:-0}" -gt 0 ] || exit 0
if (( now - \$(cat "\$STATE") >= IDLE_MINUTES * 60 )); then
  echo "\$(TZ=\${NIGHTLY_TZ:-UTC} date '+%Y-%m-%d %H:%M %Z')  idle stop: no app use for \$IDLE_MINUTES minutes" >> $LOG
  logger -t rci-autostop "idle stop after \$IDLE_MINUTES minutes"
  systemctl poweroff
fi
EOF
chmod 755 /usr/local/bin/rci-idle-stop

cat > /usr/local/bin/rci-nightly-stop <<EOF
#!/usr/bin/env bash
source "$CONF"
echo "\$(TZ=\${NIGHTLY_TZ:-UTC} date '+%Y-%m-%d %H:%M %Z')  nightly stop (\$NIGHTLY_STOP \$NIGHTLY_TZ)" >> $LOG
logger -t rci-autostop "nightly stop"
systemctl poweroff
EOF
chmod 755 /usr/local/bin/rci-nightly-stop

cat > /usr/local/bin/rci-autostop-status <<EOF
#!/usr/bin/env bash
source "$CONF"
tz=\${NIGHTLY_TZ:-UTC}
booted=\$(( \$(date +%s) - \$(cut -d. -f1 /proc/uptime) ))
echo "Now:            \$(TZ=\$tz date '+%Y-%m-%d %H:%M %Z')   (running since \$(TZ=\$tz date -d "@\$booted" '+%H:%M'))"
if [ "\${IDLE_MINUTES:-0}" -gt 0 ]; then
  if [ -f /run/rci-idle-last-active ]; then
    idle=\$(( (\$(date +%s) - \$(cat /run/rci-idle-last-active)) / 60 ))
    echo "Idle stop:      after \$IDLE_MINUTES min without app use; idle for \$idle min, so about \$(( IDLE_MINUTES - idle > 0 ? IDLE_MINUTES - idle : 0 )) min left if unused"
  else
    echo "Idle stop:      after \$IDLE_MINUTES min without app use (clock starts at the first check, 5 min after boot)"
  fi
else
  echo "Idle stop:      off"
fi
if [ -n "\${NIGHTLY_STOP:-}" ]; then
  next=\$(systemctl show rci-nightly-stop.timer -p NextElapseUSecRealtime --value)
  echo "Nightly stop:   \$NIGHTLY_STOP \$tz (next: \$(TZ=\$tz date -d "\$next" '+%a %Y-%m-%d %H:%M %Z'))"
else
  echo "Nightly stop:   off"
fi
echo "Settings:       $CONF"
echo "Past stops:"
tail -n 5 $LOG | sed 's/^/  /'
[ -s $LOG ] || echo "  (none yet)"
EOF
chmod 755 /usr/local/bin/rci-autostop-status

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
ExecStart=/usr/local/bin/rci-nightly-stop
EOF

systemctl daemon-reload
if [ -n "${NIGHTLY_STOP:-}" ]; then
  cat > /etc/systemd/system/rci-nightly-stop.timer <<EOF
[Unit]
Description=Power off at $NIGHTLY_STOP $NIGHTLY_TZ

[Timer]
OnCalendar=*-*-* $NIGHTLY_STOP:00 $NIGHTLY_TZ

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now rci-nightly-stop.timer
  systemctl restart rci-nightly-stop.timer
else
  systemctl disable --now rci-nightly-stop.timer 2>/dev/null || true
fi

systemctl enable --now rci-demo-db.service rci-backend.service rci-idle-stop.timer
systemctl --no-pager --lines=0 status rci-demo-db rci-backend | grep -E "●|Active:"
echo
rci-autostop-status
