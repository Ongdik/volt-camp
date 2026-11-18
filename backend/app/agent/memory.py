"""대화 맥락(Memory).

보관: 확정된 조건 슬롯, 직전 추천 요약, 저장 대기 중인 계획 초안.
보관하지 않음: Tool 원본 응답, API 키 · 오류 상세, 이전 턴의 중간 상태 전체.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from app.agent.state import Conditions


@dataclass
class SessionMemory:
    conditions: Conditions = field(default_factory=Conditions)
    last_summary: list[dict[str, Any]] = field(default_factory=list)   # 후보 id · 이름 · 판정 · 예상 잔량
    plan_draft: dict[str, Any] | None = None
    turns: int = 0


class MemoryStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionMemory] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SessionMemory:
        with self._lock:
            return self._sessions.setdefault(session_id, SessionMemory())

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
