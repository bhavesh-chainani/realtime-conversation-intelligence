from backend.metrics_prom import path_group


def test_path_group_static():
    assert path_group("/health") == "/health"
    assert path_group("/ready?x=1") == "/ready"


def test_path_group_sessions():
    assert path_group("/sessions/abc") == "/sessions/{id}"


def test_path_group_queue():
    assert path_group("/queue/jobs/xyz") == "/queue/..."
