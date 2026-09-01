# Production readiness checklist

For full technical context (architecture, configuration, drift, telemetry), see [DATA_SCIENCE_AND_OPS.md](./DATA_SCIENCE_AND_OPS.md).

Use this before pointing a public domain at App Runner or any internet-exposed deployment.

## Secrets and keys

- Rotate any keys that ever appeared in chat logs, screenshots, or shared branches; commit only `.env.example`, never `.env`.
- AssemblyAI and LiteLLM credentials live **only** on the server / in a secrets manager; keep `LLM_BASE_URL` and `LLM_API_KEY` server-side. The browser receives only short-lived AssemblyAI streaming tokens from your backend.
- If you use `API_AUTH_TOKEN` or JWT (`AUTH_JWKS_URL` + issuer + audience), store secrets in **AWS Secrets Manager** or SSM Parameter Store and inject at deploy time — not in plain environment variables in the console long term.

## Network and HTTP

- **CORS**: `BACKEND_CORS_ORIGINS` must list only your real frontend origins (e.g. `https://app.example.com`), not `*`.
- Prefer **HTTPS** end-to-end (App Runner provides TLS); do not serve the API over plain HTTP in production.
- Consider **AWS WAF** in front of public endpoints if you expose a stable URL.
- Make sure the backend can reach your LiteLLM proxy over a trusted network path and that proxy authentication is enforced.

## Auth and abuse

- Set `REQUIRE_API_AUTH=true` and configure either static `API_AUTH_TOKEN` (simple) or OIDC JWT validation (e.g. Cognito) for real users.
- Tune `RATE_LIMIT_PER_MINUTE` and `DAILY_REQUEST_QUOTA` for expected traffic; monitor 429 responses.

## Persistence

- **SQLite** is suitable for demo or single-instance only; file must live on durable storage if the container restarts (EFS or a single node). For multi-instance or HA, use **DynamoDB** (`STORAGE_BACKEND=dynamodb`) with a defined table and IAM policy.
- For customer history, expose a **read-only Postgres view** with only the columns the app needs; grant the backend a read-only database role and set a low statement timeout.
- Define retention: how long sessions and events are kept; export or delete per policy.

## Async jobs and workers

- With `JOB_STORE_BACKEND=dynamodb`, use **`INFERENCE_QUEUE_MODE=sqs`** and run worker tasks that scale on queue depth.
- Set SQS visibility timeout and worker concurrency so jobs complete before visibility expires; dead-letter queue for poison messages.

## Observability (Days 6–8)

- **Logs**: set `LOG_JSON=true` in production for structured JSON; include `APP_VERSION` and optional `GIT_SHA` in deploy env for traceability.
- **Readiness**: use `/health` for ALB “ping” (fast, always 200 if process up); use `/ready` with `STRICT_READINESS=true` when load balancers or orchestrators should **503** until LiteLLM and AssemblyAI configuration is present.
- **Metrics**: Prometheus text at `/metrics` when `METRICS_ENABLED=true`. Optional `METRICS_TOKEN` for bearer protection in untrusted networks.
- **Alerts**: alert on 5xx rate, p95 latency, worker queue depth / age, and daily quota saturation.

## Runbooks

- Document how to scale App Runner / worker count, how to roll back a release, and who is on call for API outages.
