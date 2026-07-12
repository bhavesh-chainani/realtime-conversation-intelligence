"""Prometheus metrics with low-cardinality path groups."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

METRIC_HTTP_REQUESTS = Counter(
    "rcv_http_requests_total",
    "HTTP requests",
    ["method", "path_group", "status"],
)
METRIC_HTTP_LATENCY = Histogram(
    "rcv_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path_group"],
    buckets=(
        0.005,
        0.01,
        0.025,
        0.05,
        0.1,
        0.25,
        0.5,
        1.0,
        2.5,
        5.0,
        10.0,
        float("inf"),
    ),
)


def path_group(path: str) -> str:
    """Map raw URL path to a bounded label set."""
    raw = path.split("?")[0]
    parts = [x for x in raw.split("/") if x]
    if not parts:
        p = "/"
    else:
        p = "/" + "/".join(parts)

    static = {
        "/",
        "/health",
        "/ready",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/config",
        "/limits",
        "/assemblyai-token",
        "/suggest",
        "/extract-customer-data",
    }
    if p in static:
        return p
    if parts and parts[0] == "sessions":
        return "/sessions/{id}"
    if parts and parts[0] == "queue":
        return "/queue/..."
    return "/other"


def observe_http_request(
    method: str, path: str, status_code: int, elapsed_s: float
) -> None:
    from . import config

    if not config.METRICS_ENABLED:
        return
    g = path_group(path)
    METRIC_HTTP_LATENCY.labels(method, g).observe(elapsed_s)
    METRIC_HTTP_REQUESTS.labels(method, g, str(status_code)).inc()
