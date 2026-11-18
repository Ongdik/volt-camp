"""대화 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from app.agent.graph import PlannerAgent
from app.api import deps
from app.db.plans import PlanRepository
from app.schemas.api import ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["대화"])


@router.post("/chat", response_model=ChatResponse, summary="대화 1턴 처리")
async def chat(req: ChatRequest, planner: PlannerAgent = Depends(deps.planner),
               plans: PlanRepository = Depends(deps.plans)) -> ChatResponse:
    """메인 Agent를 실행한다. 저장 의도(CONFIRM_PLAN)면 직전 추천 계획을 저장하고 겹침과 함께 돌려준다."""
    result = await run_in_threadpool(planner.run, req.message, req.session_id)
    if result.get("action") == "SAVE_PLAN" and result.get("plan_draft"):
        saved, overlaps = plans.create(result["plan_draft"], session_id=result["session_id"])
        result["saved_plan"], result["overlaps"] = saved, overlaps
        if overlaps:
            names = ", ".join(o["title"] for o in overlaps)
            result["answer"] += f"\n\n같은 기간에 이미 저장된 계획이 있어요: {names}"
    return ChatResponse(**{k: v for k, v in result.items() if k in ChatResponse.model_fields})
