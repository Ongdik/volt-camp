"""캘린더 API. Agent와 같은 Tool(list_schedule · register_plan_to_calendar)을 거쳐 입출력 형식이 같다."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api import deps
from app.api.errors import ApiError
from app.db.plans import PlanRepository
from app.tools.base import ToolFailure, ToolResult
from app.tools.local.calendar import ListScheduleTool, RegisterPlanTool

router = APIRouter(tags=["캘린더"])

STATUS = {"NO_RESULT": 404, "INVALID_INPUT": 422, "MISSING_API_KEY": 503, "AUTH_FAILED": 503,
          "QUOTA_EXCEEDED": 429}


def _unwrap(result: ToolResult) -> dict:
    if result.ok:
        return result.data
    raise ApiError(STATUS.get(result.error.code, 502), result.error.code, result.error.message)


@router.get("/api/calendar/events", summary="기간과 겹치는 캘린더 일정 (list_schedule Tool)")
def list_events(start_at: str = Query(description="YYYY-MM-DDTHH:MM (KST)"),
                end_at: str = Query(description="YYYY-MM-DDTHH:MM (KST)"),
                calendar=Depends(deps.calendar)) -> dict:
    return _unwrap(ListScheduleTool(calendar).invoke({"start_at": start_at, "end_at": end_at}))


@router.post("/api/plans/{plan_id}/calendar", summary="계획을 캘린더에 등록 (register_plan_to_calendar Tool)")
def register_plan(plan_id: int, calendar=Depends(deps.calendar),
                  plans: PlanRepository = Depends(deps.plans)) -> dict:
    return _unwrap(RegisterPlanTool(calendar, plans).invoke({"plan_id": plan_id}))


@router.get("/api/plans/{plan_id}/calendar", summary="계획의 캘린더 등록 여부")
def plan_calendar(plan_id: int, calendar=Depends(deps.calendar),
                  plans: PlanRepository = Depends(deps.plans)) -> dict:
    if plans.get(plan_id) is None:
        raise ApiError(404, "PLAN_NOT_FOUND", f"{plan_id}번 계획을 찾을 수 없습니다")
    try:
        event = calendar.find_by_plan(plan_id)
    except ToolFailure as f:
        return {"provider": calendar.name, "registered": None, "event": None, "error": f.message}
    return {"provider": calendar.name, "registered": event is not None,
            "event": event.model_dump() if event else None, "error": None}
