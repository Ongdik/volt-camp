"""LLM(OpenAI SDK) 예외 → 오류 코드 · 사용자 문구 변환."""

import httpx
import openai
import pytest

from app.agent.llm import llm_failure
from app.tools.base import ErrorCode, ToolFailure

REQ = httpx.Request("POST", "https://llm.example/v1/responses")


def status_error(cls, code, body):
    return cls(str(body), response=httpx.Response(code, request=REQ), body=body)


@pytest.mark.parametrize("exc, code, phrase", [
    (status_error(openai.AuthenticationError, 401,
                  {"message": "[GMS 에러] This GMS key has no token left", "statusCode": 401}),
     ErrorCode.QUOTA_EXCEEDED, "소진"),
    (status_error(openai.RateLimitError, 429, {"message": "rate limit"}), ErrorCode.QUOTA_EXCEEDED, "소진"),
    (status_error(openai.AuthenticationError, 401, {"message": "invalid api key"}), ErrorCode.AUTH_FAILED, "OPENAI_API_KEY"),
    (status_error(openai.InternalServerError, 503, {"message": "down"}), ErrorCode.UPSTREAM_ERROR, "503"),
    (openai.APITimeoutError(request=REQ), ErrorCode.TIMEOUT, "늦어요"),
    (openai.APIConnectionError(request=REQ), ErrorCode.UPSTREAM_ERROR, "연결하지 못했어요"),
    (ValueError("schema mismatch"), ErrorCode.INTERNAL_ERROR, "문제가 생겼어요"),
])
def test_llm_failure_maps_code_and_message(exc, code, phrase):
    f = llm_failure(exc)
    assert isinstance(f, ToolFailure)
    assert f.code == code
    assert phrase in f.message


def test_tool_failure_passes_through():
    original = ToolFailure(ErrorCode.MISSING_API_KEY, "키 없음")
    assert llm_failure(original) is original
