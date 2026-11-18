"""테스트 공통: 실제 응답 샘플(tests/fixtures) 로딩, 가짜 HTTP 응답, 테스트용 설정."""

import json
import os
from pathlib import Path

os.environ.setdefault("VOLTCAMP_NO_AUTOAPP", "1")   # 테스트에서 실제 앱(DB 파일 · 실제 Agent)을 만들지 않는다

import pytest
import requests

from app.config import get_settings

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload, ensure_ascii=False)
        self.content = self.text.encode("utf-8")

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture(autouse=True)
def test_settings(monkeypatch):
    """모든 테스트는 가짜 키로 돌고, 실제 네트워크 호출은 막는다."""
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "test-data-key")
    monkeypatch.setenv("KAKAO_REST_API_KEY", "test-kakao-key")
    monkeypatch.setenv("TOOL_TIMEOUT_SEC", "3")
    get_settings.cache_clear()

    def no_network(*args, **kwargs):
        raise AssertionError("테스트에서 실제 네트워크를 호출했습니다")

    monkeypatch.setattr(requests, "get", no_network)
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_get(monkeypatch):
    """`fake_get(응답 또는 함수)`로 requests.get을 바꾸고, 호출 기록을 돌려준다."""
    calls = []

    def install(response):
        def _get(url, params=None, headers=None, timeout=None):
            calls.append({"url": url, "params": params or {}, "headers": headers or {}})
            if callable(response):
                return response(url, params or {})
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setattr(requests, "get", _get)
        return calls

    return install
