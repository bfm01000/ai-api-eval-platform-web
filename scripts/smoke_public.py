"""Verify a running public deployment without using keys or calling a provider."""

import json
import sys
import urllib.error
import urllib.request


def verify(base_url: str) -> None:
    def request(path, body=None):
        payload = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            base_url.rstrip("/") + path,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.read().decode(), response.headers

    status, text, _ = request("/health")
    assert status == 200 and json.loads(text)["status"] == "ok", "Health check failed"
    status, text, headers = request("/api/test-config")
    assert status == 200 and json.loads(text) == {"mode": "public", "providers": []}
    assert headers["Cache-Control"] == "no-store"
    for path in ("/api/providers", "/api/runs", "/api/runs/private/results"):
        assert request(path)[0] == 404, f"Public history/config exposed at {path}"
    for path in ("/api/provider-tests", "/api/runs"):
        assert request(path, {"provider_id": "test_provider"})[0] == 404
    for path in ("/api/self-tests", "/api/self-runs"):
        status, text, _ = request(
            path,
            {
                "endpoint": "https://[::1]/v1/chat/completions",
                "api_key": "smoke-placeholder-only",
                "model": "smoke-model",
            },
        )
        assert status == 422, f"Private endpoint accepted at {path}"
        assert "smoke-placeholder-only" not in text, "Validation response exposed input"
    print("PASS: health, public mode, private routes, IPv6 rejection and safe errors")


if __name__ == "__main__":
    verify(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
