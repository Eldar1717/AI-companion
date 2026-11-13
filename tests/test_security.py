"""Local API protection tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend import config, security


def test_safe_join_allows_normal_path(tmp_path: Path):
    target = security.safe_join(tmp_path, "css/style.css")
    assert target == (tmp_path / "css" / "style.css").resolve()


@pytest.mark.parametrize(
    "path",
    ["../app.py", "css/../../app.py", "/etc/passwd", "css/..\\..\\app.py", "a\x00b"],
)
def test_safe_join_rejects_traversal(tmp_path: Path, path: str):
    with pytest.raises(security.SecurityError):
        security.safe_join(tmp_path, path)


def test_cross_origin_request_is_rejected(client):
    response = client.post("/api/chat", json={"message": "x"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.get_json()["error"] == "UNAUTHORIZED"


def test_same_origin_request_is_allowed(client):
    origin = f"http://127.0.0.1:{config.PORT}"
    response = client.get("/api/health", headers={"Origin": origin})
    assert response.status_code == 200


def test_referer_from_same_origin_is_allowed(client):
    origin = f"http://127.0.0.1:{config.PORT}"
    response = client.get("/api/health", headers={"Referer": origin + "/"})
    assert response.status_code == 200


def test_non_browser_request_without_secret_is_allowed(client):
    # no Origin/Referer and no AI_COMPANION_SECRET configured -> loopback clients ok
    response = client.get("/api/health")
    assert response.status_code == 200


def test_static_traversal_is_blocked(client):
    for path in ("/assets/../app.py", "/assets/%2e%2e/app.py", "/assets/../../etc/passwd"):
        response = client.get(path)
        assert response.status_code in (403, 404)


def test_security_headers_present(client):
    response = client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"


def test_api_requires_json_body(client):
    response = client.post("/api/chat", data="not json", content_type="text/plain")
    assert response.status_code == 400
