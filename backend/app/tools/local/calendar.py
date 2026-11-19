"""캘린더 Tool (로컬 서버 API 툴). 구현체(로컬 저장소 · Google Calendar)가 바뀌어도 입출력 형식은 같다.

- list_schedule: 기간을 주면 그 기간과 겹치는 일정 목록
- register_plan_to_calendar: 저장된 계획의 번호 하나만 받는다. 제목 · 출발 · 귀가 시각은 계획 저장소에서 읽어
  LLM이 값을 지어내 넣을 수 없게 한다. 같은 계획을 다시 등록하면 기존 일정을 돌려준다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.calendars.base import CalendarProvider
from app.db.plans import PlanRepository
from app.tools.base import ErrorCode, Tool, ToolFailure, ToolMeta

TIME_FMT = "%Y-%m-%dT%H:%M"


def _minute(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime(TIME_FMT)
    except ValueError as exc:
        raise ValueError("YYYY-MM-DDTHH:MM 형식이어야 합니다") from exc


class ScheduleInput(BaseModel):
    start_at: str = Field(description="조회 시작 (KST, YYYY-MM-DDTHH:MM)")
    end_at: str = Field(description="조회 끝 (KST, YYYY-MM-DDTHH:MM, 포함하지 않음)")

    @field_validator("start_at", "end_at")
    @classmethod
    def minute(cls, value: str) -> str:
        return _minute(value)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_at <= self.start_at:
            raise ValueError("end_at은 start_at보다 뒤여야 합니다")
        return self


class ListScheduleTool(Tool):
    name = "list_schedule"
    description = ("차박 기간에 사용자의 다른 일정이 있는지 확인해야 할 때 쓴다. 기간과 겹치는 캘린더 일정 목록을 돌려준다. "
                   "일정이 없으면 빈 목록(실패 아님).")
    input_model = ScheduleInput
    kind = "local"

    def __init__(self, calendar: CalendarProvider):
        self.calendar = calendar
        self.source = f"캘린더({calendar.name})"

    def run(self, args: ScheduleInput, meta: ToolMeta) -> dict[str, Any]:
        events = self.calendar.list_events(args.start_at, args.end_at)
        meta.actual = {"provider": self.calendar.name}
        return {"provider": self.calendar.name, "range": [args.start_at, args.end_at],
                "events": [e.model_dump() for e in events], "count": len(events)}


class RegisterPlanInput(BaseModel):
    plan_id: int = Field(gt=0, description="저장된 차박 계획 번호. 제목 · 시각은 서버가 계획에서 읽는다")


class RegisterPlanTool(Tool):
    name = "register_plan_to_calendar"
    description = ("사용자가 저장한 차박 계획을 캘린더에 등록해 달라고 할 때 쓴다. 계획 번호만 넘긴다. "
                   "이미 등록된 계획이면 새로 만들지 않고 기존 일정을 돌려준다.")
    input_model = RegisterPlanInput
    kind = "local"

    def __init__(self, calendar: CalendarProvider, plans: PlanRepository):
        self.calendar = calendar
        self.plans = plans
        self.source = f"캘린더({calendar.name})"

    @staticmethod
    def _description(plan: dict[str, Any]) -> str:
        camp = plan.get("campsite") or {}
        lines = [f"캠핑장: {camp.get('name', '-')} ({camp.get('address', '-')})", f"출발지: {plan.get('origin') or '-'}"]
        soc = plan.get("expected_soc") or {}
        if soc:
            lines.append("예상 잔량: 도착 {:.0%} · 캠핑 후 {:.0%} · 귀가 {:.0%}".format(
                soc.get("arrive", 0), soc.get("after_camp", 0), soc.get("return", 0)))
        stop = plan.get("charge_stop")
        if stop:
            lines.append(f"충전 경유: {stop['station']['name']} {stop['charge_kwh']}kWh (약 {stop['charge_minutes']}분)")
        if plan.get("memo"):
            lines.append(f"메모: {plan['memo']}")
        lines.append(f"VoltCamp 계획 #{plan['id']}")
        return "\n".join(lines)

    def run(self, args: RegisterPlanInput, meta: ToolMeta) -> dict[str, Any]:
        plan = self.plans.get(args.plan_id)
        if plan is None:
            raise ToolFailure(ErrorCode.NO_RESULT, f"{args.plan_id}번 계획이 없습니다")
        meta.actual = {"provider": self.calendar.name}
        existing = self.calendar.find_by_plan(plan["id"])
        if existing:
            meta.notes.append("이미 등록된 계획이라 새로 만들지 않았습니다")
            return {"provider": self.calendar.name, "already_registered": True, "event": existing.model_dump(),
                    "plan_id": plan["id"]}
        event = self.calendar.create_event(plan["title"], plan["start_at"], plan["end_at"], plan["id"],
                                           self._description(plan))
        return {"provider": self.calendar.name, "already_registered": False, "event": event.model_dump(),
                "plan_id": plan["id"]}
