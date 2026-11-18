"""Agent가 쓰는 LLM 호출 두 가지: 요청 분석(구조화 출력), 답변 문장 쓰기.

LangChain `ChatOpenAI`를 Responses API 모드로 쓴다. 일부 추론형 모델은 Chat Completions에서
function tool · 구조화 출력을 거부하기 때문이다 (연결 확인 #1에서 확인).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Protocol

from app.agent import prompts
from app.agent.state import Analysis
from app.config import get_settings
from app.tools.base import ErrorCode, ToolFailure

logger = logging.getLogger(__name__)

QUOTA_HINTS = ("no token left", "quota", "insufficient", "limit")


def llm_failure(e: Exception) -> ToolFailure:
    """OpenAI SDK 예외를 Tool과 같은 오류 코드로 바꾼다. 사용자 문구는 원인별로 다르게."""
    import openai

    detail = str(e).lower()
    if isinstance(e, ToolFailure):
        return e
    if isinstance(e, openai.RateLimitError) or (
            isinstance(e, openai.AuthenticationError) and any(h in detail for h in QUOTA_HINTS)):
        return ToolFailure(ErrorCode.QUOTA_EXCEEDED, "LLM 사용량(토큰)이 모두 소진돼 지금은 요청을 분석할 수 없어요. "
                                                     "API 키의 사용량을 충전하거나 새 키로 바꾼 뒤 다시 시도해 주세요.")
    if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ToolFailure(ErrorCode.AUTH_FAILED, "LLM API 키 인증에 실패했어요. 서버의 OPENAI_API_KEY 설정을 확인해 주세요.")
    if isinstance(e, openai.APITimeoutError):
        return ToolFailure(ErrorCode.TIMEOUT, "LLM 응답이 너무 늦어요. 잠시 후 다시 말씀해 주세요.")
    if isinstance(e, openai.APIConnectionError):
        return ToolFailure(ErrorCode.UPSTREAM_ERROR, "LLM 서버에 연결하지 못했어요. 네트워크나 OPENAI_BASE_URL을 확인해 주세요.")
    if isinstance(e, openai.APIStatusError):
        return ToolFailure(ErrorCode.UPSTREAM_ERROR, f"LLM 서버가 오류를 돌려줬어요 (HTTP {e.status_code}). 잠시 후 다시 말씀해 주세요.")
    return ToolFailure(ErrorCode.INTERNAL_ERROR, "요청을 분석하는 중 문제가 생겼어요. 잠시 후 다시 말씀해 주세요.")


class AgentLLM(Protocol):
    def analyze(self, message: str, memory_summary: str, today: date) -> Analysis: ...

    def write(self, kind: str, context: dict[str, Any]) -> str: ...


class OpenAIAgentLLM:
    def __init__(self) -> None:
        s = get_settings()
        if not s.openai_api_key or not s.llm_model:
            raise ToolFailure(ErrorCode.MISSING_API_KEY, "LLM API 키 또는 모델이 설정되지 않았습니다")
        from langchain_openai import ChatOpenAI

        self.chat = ChatOpenAI(model=s.llm_model, api_key=s.openai_api_key, base_url=s.openai_base_url,
                               use_responses_api=True, timeout=60, max_retries=1)

    def analyze(self, message: str, memory_summary: str, today: date) -> Analysis:
        structured = self.chat.with_structured_output(Analysis)
        try:
            return structured.invoke([
                ("system", prompts.analyze_system(today)),
                ("human", f"[이전 대화 조건]\n{memory_summary}\n\n[사용자 메시지]\n{message}"),
            ])
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 요청 분석 실패: %s: %s", type(e).__name__, e)
            raise llm_failure(e) from e

    def write(self, kind: str, context: dict[str, Any]) -> str:
        try:
            msg = self.chat.invoke([
                ("system", prompts.WRITE_SYSTEM[kind]),
                ("human", json.dumps(context, ensure_ascii=False, default=str)),
            ])
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 답변 작성 실패: %s: %s", type(e).__name__, e)
            raise llm_failure(e) from e
        return msg.text.strip()


DIAGNOSE_SYSTEM = """너는 AI Agent 실행 로그를 점검하는 진단기다. 규칙 검사에서 걸린 항목(findings)과 그 턴의 이벤트를 보고,
원인이 어느 쪽(메인 Agent의 계획 · 재계획 규칙, Tool, 외부 API, 사용자 조건)에 있는지와 무엇을 고치면 되는지를
한국어 3문장 이내로 쓴다. 이벤트에 없는 사실은 추측하지 않는다."""


def make_diagnoser():
    """이상(WARN) 판정 턴의 원인 진단기. LLM 키가 없으면 None (판정만 하고 진단은 하지 않는다)."""
    s = get_settings()
    if not s.openai_api_key or not s.llm_model:
        return None
    from langchain_openai import ChatOpenAI

    chat = ChatOpenAI(model=s.llm_model, api_key=s.openai_api_key, base_url=s.openai_base_url,
                      use_responses_api=True, timeout=60, max_retries=1)

    def diagnose(events: list[dict], findings: list[dict]) -> str:
        brief = [{k: e.get(k) for k in ("seq", "type", "node", "payload")} for e in events][-80:]
        msg = chat.invoke([("system", DIAGNOSE_SYSTEM),
                           ("human", json.dumps({"findings": findings, "events": brief}, ensure_ascii=False, default=str))])
        return msg.text.strip()

    return diagnose
