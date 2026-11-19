"""실제 Tool · LLM · 지오코더로 메인 Agent를 조립한다."""

from __future__ import annotations

from app.agent.graph import PlannerAgent
from app.calendars.base import CalendarProvider
from app.agent.llm import OpenAIAgentLLM
from app.agent.memory import MemoryStore
from app.clients import kakao
from app.config import get_settings
from app.db.plans import PlanRepository
from app.events.bus import EventBus
from app.tools.external.campsite import SearchCampsitesTool
from app.tools.external.charger import SearchChargersTool
from app.tools.external.route import GetRouteTool
from app.tools.external.weather import GetWeatherTool
from app.tools.local.calendar import ListScheduleTool, RegisterPlanTool
from app.tools.registry import ToolRegistry


def default_registry(calendar: CalendarProvider | None = None, plans: PlanRepository | None = None) -> ToolRegistry:
    """외부 API Tool 4종 + (캘린더가 있으면) 일정 조회 · 계획 등록 로컬 Tool."""
    tools = [SearchCampsitesTool(), GetRouteTool(), GetWeatherTool(), SearchChargersTool()]
    if calendar is not None:
        tools.append(ListScheduleTool(calendar))
        if plans is not None:
            tools.append(RegisterPlanTool(calendar, plans))
    return ToolRegistry(tools)


def build_planner(bus: EventBus | None = None, memory: MemoryStore | None = None,
                  calendar: CalendarProvider | None = None, plans: PlanRepository | None = None) -> PlannerAgent:
    return PlannerAgent(default_registry(calendar, plans), OpenAIAgentLLM, kakao.geocode, bus=bus, memory=memory,
                        max_replan=get_settings().max_replan)
