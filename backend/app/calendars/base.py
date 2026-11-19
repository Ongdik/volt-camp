"""캘린더 구현체가 지켜야 할 형식.

Tool(`list_schedule`, `register_plan_to_calendar`)은 이 인터페이스만 보고 동작하므로,
로컬 저장소 구현체를 Google Calendar 구현체로 바꿔도 Tool의 입출력 형식은 그대로다.

시각은 모두 한국 시간(KST) 기준 'YYYY-MM-DDTHH:MM' 문자열로 주고받는다 (계획 저장소와 같은 형식).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field

from app.clients.kma import KST


class CalendarEvent(BaseModel):
    event_id: str = Field(description="캘린더 쪽 일정 식별자")
    title: str
    start_at: str = Field(description="시작 (KST, YYYY-MM-DDTHH:MM)")
    end_at: str = Field(description="끝 (KST, YYYY-MM-DDTHH:MM)")
    plan_id: int | None = Field(default=None, description="VoltCamp 계획에서 등록한 일정이면 그 계획 번호")
    all_day: bool = False


class CalendarProvider(Protocol):
    name: str   # local · google

    def list_events(self, start_at: str, end_at: str) -> list[CalendarEvent]:
        """[start_at, end_at)과 겹치는 일정 (시작 시각 순)."""

    def find_by_plan(self, plan_id: int) -> CalendarEvent | None:
        """그 계획으로 이미 등록한 일정."""

    def create_event(self, title: str, start_at: str, end_at: str, plan_id: int, description: str) -> CalendarEvent:
        """일정 등록. 실패는 ToolFailure로 알린다."""


def to_local(value: datetime) -> str:
    """aware datetime → KST 'YYYY-MM-DDTHH:MM'. naive면 이미 KST로 본다."""
    if value.tzinfo is not None:
        value = value.astimezone(KST)
    return value.strftime("%Y-%m-%dT%H:%M")


def to_aware(value: str) -> datetime:
    """'YYYY-MM-DDTHH:MM'(KST) → aware datetime."""
    return datetime.fromisoformat(value).replace(tzinfo=KST)
