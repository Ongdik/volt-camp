"""차박 계획 관리 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.api import deps
from app.api.errors import ApiError
from app.db.plans import PlanRepository
from app.schemas.api import PlanCreate, PlanResult, PlanUpdate, iso

router = APIRouter(prefix="/api/plans", tags=["계획"])


def _not_found(plan_id: int) -> ApiError:
    return ApiError(404, "PLAN_NOT_FOUND", f"{plan_id}번 계획을 찾을 수 없습니다")


@router.get("", summary="계획 목록 (출발 일시 순)")
def list_plans(plans: PlanRepository = Depends(deps.plans)) -> list[dict]:
    return plans.list()


@router.post("", response_model=PlanResult, status_code=201, summary="계획 저장")
def create_plan(body: PlanCreate, plans: PlanRepository = Depends(deps.plans)) -> PlanResult:
    data = body.model_dump()
    data["start_at"], data["end_at"] = iso(body.start_at), iso(body.end_at)
    plan, overlaps = plans.create(data)
    return PlanResult(plan=plan, overlaps=overlaps)


@router.get("/{plan_id}", summary="계획 상세")
def get_plan(plan_id: int, plans: PlanRepository = Depends(deps.plans)) -> dict:
    plan = plans.get(plan_id)
    if plan is None:
        raise _not_found(plan_id)
    return plan


@router.put("/{plan_id}", response_model=PlanResult, summary="계획 수정 (기간 · 기기 · 메모 · 제목 · 출발지)")
def update_plan(plan_id: int, body: PlanUpdate, plans: PlanRepository = Depends(deps.plans)) -> PlanResult:
    changes = body.model_dump(exclude_none=True)
    for key in ("start_at", "end_at"):
        if key in changes:
            changes[key] = iso(changes[key])
    result = plans.update(plan_id, changes)
    if result is None:
        raise _not_found(plan_id)
    return PlanResult(plan=result[0], overlaps=result[1])


@router.delete("/{plan_id}", status_code=204, summary="계획 삭제")
def delete_plan(plan_id: int, plans: PlanRepository = Depends(deps.plans)) -> Response:
    if not plans.delete(plan_id):
        raise _not_found(plan_id)
    return Response(status_code=204)
