"""Agent가 쓰는 LLM 호출 두 가지: 요청 분석(구조화 출력), 답변 문장 쓰기.

LangChain `ChatOpenAI`를 Responses API 모드로 쓴다. 일부 추론형 모델은 Chat Completions에서
function tool · 구조화 출력을 거부하기 때문이다 (연결 확인 #1에서 확인).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Protocol

from app.agent import prompts
from app.agent.state import Analysis
from app.config import get_settings
from app.tools.base import ErrorCode, ToolFailure


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
        return structured.invoke([
            ("system", prompts.analyze_system(today)),
            ("human", f"[이전 대화 조건]\n{memory_summary}\n\n[사용자 메시지]\n{message}"),
        ])

    def write(self, kind: str, context: dict[str, Any]) -> str:
        msg = self.chat.invoke([
            ("system", prompts.WRITE_SYSTEM[kind]),
            ("human", json.dumps(context, ensure_ascii=False, default=str)),
        ])
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
