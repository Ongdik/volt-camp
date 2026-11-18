"""라우터가 쓰는 공용 객체 (app.state에서 꺼낸다 — 테스트에서 바꿔 끼우기 쉽다)."""

from __future__ import annotations

from fastapi import Request

from app.agent.graph import PlannerAgent
from app.db.plans import PlanRepository
from app.db.run_logs import RunLogRepository


def planner(request: Request) -> PlannerAgent:
    return request.app.state.planner


def plans(request: Request) -> PlanRepository:
    return request.app.state.plans


def run_logs(request: Request) -> RunLogRepository:
    return request.app.state.run_logs
