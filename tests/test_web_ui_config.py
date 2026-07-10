"""Web UI hardening invariants: same-origin by default, explicit CORS opt-in."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest  # noqa: E402

# web_client imports the optional LLM SDKs (llm-sdks dependency group);
# skip cleanly in environments that only installed the test group.
web_client = pytest.importorskip("web_client")
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402


def _has_cors_middleware(app) -> bool:
    return any(m.cls is CORSMiddleware for m in app.user_middleware)


def test_parse_cors_origins():
    assert web_client._parse_cors_origins("") == []
    assert web_client._parse_cors_origins(None) == []
    assert web_client._parse_cors_origins("https://a.example") == ["https://a.example"]
    assert web_client._parse_cors_origins(" https://a.example , https://b.example ,") == [
        "https://a.example",
        "https://b.example",
    ]


def test_app_has_no_cors_middleware_by_default(monkeypatch):
    """WEB_UI_CORS_ORIGINS unset -> the UI is same-origin only. This guards
    against reintroducing the wildcard allow_origins + allow_credentials
    combination that security reviews reject."""
    assert not _has_cors_middleware(web_client.app)
