"""실제 Tool · LLM · 지오코더로 메인 Agent를 조립한다."""

from __future__ import annotations

from app.agent.graph import PlannerAgent
from app.agent.llm import OpenAIAgentLLM
from app.agent.memory import MemoryStore
from app.clients import kakao
from app.config import get_settings
from app.events.bus import EventBus
from app.tools.external.campsite import SearchCampsitesTool
from app.tools.external.charger import SearchChargersTool
from app.tools.external.route import GetRouteTool
from app.tools.external.weather import GetWeatherTool
from app.tools.registry import ToolRegistry


def default_registry() -> ToolRegistry:
    return ToolRegistry([SearchCampsitesTool(), GetRouteTool(), GetWeatherTool(), SearchChargersTool()])


def build_planner(bus: EventBus | None = None, memory: MemoryStore | None = None) -> PlannerAgent:
    return PlannerAgent(default_registry(), OpenAIAgentLLM, kakao.geocode, bus=bus, memory=memory,
                        max_replan=get_settings().max_replan)
