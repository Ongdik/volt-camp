"""실행 이벤트 모델."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    TURN_START = "TURN_START"        # 사용자 메시지 1건 처리 시작
    TURN_END = "TURN_END"            # 처리 끝 (소요 시간, 최종 상태)
    NODE_ENTER = "NODE_ENTER"        # 실행 단계 진입
    NODE_EXIT = "NODE_EXIT"          # 실행 단계 종료 (산출 요약)
    LLM_DECISION = "LLM_DECISION"    # LLM이 실행 흐름을 바꾸는 판단 + 이유
    TOOL_CALL = "TOOL_CALL"          # Tool 호출 요청 (인자, 캐시 사용 여부)
    TOOL_RESULT = "TOOL_RESULT"      # Tool 응답 (성공 여부, 실패 코드, 소요 시간)
    JUDGE_RESULT = "JUDGE_RESULT"    # 후보별 제약 판정
    REPLAN = "REPLAN"                # 재계획 (횟수, 원인, 전략)
    ERROR = "ERROR"                  # 실행 중 오류 (사용자용 메시지만)


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str
    turn_id: str
    seq: int
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
    type: EventType
    node: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
