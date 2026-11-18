"""API 요청 · 응답 모델 (FastAPI /docs에 그대로 나타난다)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=64, description="이어서 대화할 세션. 없으면 새로 만든다")
    message: str = Field(min_length=1, max_length=1000)


class ChatResponse(BaseModel):
    session_id: str
    turn_id: str
    status: str = Field(description="OK · NEED_INFO · NO_OPTION · LIMIT_REACHED · PARTIAL · ERROR")
    intent: str | None = None
    intent_reason: str | None = None
    answer: str
    conditions: dict[str, Any] = {}
    changed: list[str] = []
    missing: list[str] = []
    recommendations: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    assumptions: list[str] = []
    suggestions: list[str] = []
    replans: list[dict[str, Any]] = []
    tool_failures: list[dict[str, Any]] = []
    notices: list[str] = []
    plan_draft: dict[str, Any] | None = None
    action: str | None = None
    saved_plan: dict[str, Any] | None = None
    overlaps: list[dict[str, Any]] = []
    error_code: str | None = None
    elapsed_ms: int
    timeline: list[dict[str, Any]] = []


class PlanCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    campsite: dict[str, Any]
    origin: str | None = None
    start_at: datetime
    end_at: datetime
    vehicle: dict[str, Any]
    devices: list[dict[str, Any]] = []
    charge_stop: dict[str, Any] | None = None
    expected_soc: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    memo: str = Field(default="", max_length=500)


class PlanUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    origin: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    devices: list[dict[str, Any]] | None = None
    memo: str | None = Field(default=None, max_length=500)


def iso(dt: datetime | None) -> str | None:
    return dt.replace(tzinfo=None).isoformat(timespec="minutes") if dt else None


class PlanResult(BaseModel):
    plan: dict[str, Any]
    overlaps: list[dict[str, Any]] = Field(description="기간이 겹치는 다른 계획 (저장은 됨)")


class Health(BaseModel):
    llm: bool
    data_go_kr: bool
    kakao: bool

    @field_validator("*")
    @classmethod
    def only_bool(cls, v):
        return bool(v)
