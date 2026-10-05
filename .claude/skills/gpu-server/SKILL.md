---
name: gpu-server
description: Start, deploy to, connect to, check or stop the AWS GPU server (rci-gpu) that runs the backend with Nemotron diarisation. Use for anything about the GPU host, deploying the backend, or running Nemotron at full speed.
---

# AWS GPU server

The server is EC2 `rci-gpu`:
- g4dn.xlarge with an NVIDIA T4, in ap-southeast-2b (instance `i-0ed4bf54b88357229`);
- user `ubuntu`, SSH key `~/.ssh/rci-gpu.pem`;
- it costs about **US$1/hour while running**.

Everything goes through `scripts/gpu.sh`, which uses the AWS CLI configured on the laptop. Settings are in
`scripts/gpu_host.conf`.

```bash
scripts/gpu.sh status     # state, IP; when running, the auto-stop status
scripts/gpu.sh start      # start and wait for SSH; prints the new public IP (changes on every start)
scripts/gpu.sh deploy     # rsync this checkout to ~/rci, pip install, regenerate services, restart, wait for /ready
scripts/gpu.sh connect    # tunnel the server's backend to localhost:8000 (blocks; Ctrl+C closes)
scripts/gpu.sh stop       # stop the instance; disk, .env, weights and demo DB are kept
```

## Rules

- **Starting the instance costs money.** Only start it when the user asked for it or for a task that needs
  it. Stop it when you are done, unless the user wants it left running.
- **The server auto-stops** after `IDLE_MINUTES` (45) without app use and at `NIGHTLY_STOP` (02:00 SGT).
  Stops are logged to `/var/log/rci-autostop.log`.
- **`deploy` keeps the server's own files:** `.env` (with `DIARIZATION_DEVICE=cuda`), `data/` (weights and
  demo DB) and `.venv`. Never copy the laptop's `.env` over it.
- **Check after a deploy:** `scripts/gpu.sh deploy` waits for `/ready` to show
  `"diarization":{"ready":true,"mode":"low_latency"}`.
  - If it times out, read the logs: `ssh -i ~/.ssh/rci-gpu.pem ubuntu@<ip> journalctl -u rci-backend -n 80`.
  - CUDA library clashes show up as `CUDNN_STATUS_SUBLIBRARY_LOADING_FAILED`. The services clear
    `LD_LIBRARY_PATH` for that reason.
- **If SSH times out,** the security group may only allow the user's home IP. Ask the user to update the
  inbound rule.
- **If `start` fails with `InsufficientInstanceCapacity`,** AWS has no spare GPUs of that type in that zone.
  - Capacity usually returns within hours.
  - A stopped instance can change type in place (`aws ec2 modify-instance-attribute --instance-type`), but
    not zone. Moving zones means making an AMI and launching a copy, as was done on 2026-10-05.
  - Ask before launching or resizing anything: both cost money.
  - The account's vCPU quota for G instances blocks 8-vCPU sizes such as g6.2xlarge (`VcpuLimitExceeded`).
    The user would need to request a quota increase.
- **The previous server is kept stopped as `rci-gpu-old`** (`i-099caacb64a77b8e6`, zone 2a), together with
  AMI `ami-0409883812559ce71`. Delete them only when the user says so.

## Using it for a live call

1. `scripts/gpu.sh start`
2. `scripts/gpu.sh deploy`, if the code changed
3. `scripts/gpu.sh connect`, in its own terminal
4. On the laptop, stop any local backend on :8000, then `cd frontend && npm run dev`.
