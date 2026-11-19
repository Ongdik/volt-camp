"""FastAPI 앱: DB · 이벤트 버스 · 로깅 Agent · 메인 Agent를 조립한다.

실행 (backend/에서): uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.factory import build_planner
from app.agent.graph import PlannerAgent
from app.agent.llm import make_diagnoser
from app.agent.memory import MemoryStore
from app.api import calendar, chat, errors, health, logs, plans
from app.calendars import make_calendar
from app.config import get_settings
from app.db.anomalies import AnomalyRepository
from app.db.database import Database
from app.db.plans import PlanRepository
from app.db.run_logs import RunLogRepository
from app.events.bus import EventBus
from app.logging_agent.agent import LoggingAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(planner: PlannerAgent | None = None, db: Database | None = None, bus: EventBus | None = None,
               diagnoser=None, background_diagnosis: bool = True, calendar_provider=None) -> FastAPI:
    app = FastAPI(title="VoltCamp API", version="0.1.0",
                  description="전기차 V2L 차박 전력 플래너 — 대화 · 계획 관리 · 실행 로그")
    db = db or Database(get_settings().db_path)
    bus = bus or EventBus()
    app.state.db = db
    app.state.bus = bus
    app.state.run_logs = RunLogRepository(db)
    app.state.plans = PlanRepository(db)
    app.state.anomalies = AnomalyRepository(db)
    app.state.calendar = calendar_provider or make_calendar(db)
    app.state.logging_agent = LoggingAgent(
        app.state.run_logs, app.state.anomalies,
        diagnoser=diagnoser if diagnoser is not None or planner is not None else make_diagnoser(),
        max_replan=get_settings().max_replan, background=background_diagnosis).attach(bus)
    app.state.planner = planner or build_planner(bus=bus, memory=MemoryStore(), calendar=app.state.calendar,
                                                 plans=app.state.plans)
    app.state.planner.warning_lookup = app.state.anomalies.latest_warning
    app.state.planner.plan_lookup = app.state.plans.latest_for_session

    origins = os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins if o.strip()],
                       allow_methods=["*"], allow_headers=["*"])
    errors.install(app)
    for router in (chat.router, plans.router, calendar.router, logs.router, health.router):
        app.include_router(router)
    return app


app = create_app() if os.getenv("VOLTCAMP_NO_AUTOAPP") != "1" else None
