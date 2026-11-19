"""캘린더 연동 (F3023): 일정을 제약 판단에 반영, 대화로 계획 등록."""

from app.agent.graph import PlannerAgent
from app.calendars.local import LocalCalendarProvider
from app.db.database import Database
from app.db.plans import PlanRepository
from app.logging_agent.anomaly import judge_turn
from app.tools.base import ErrorCode, ToolFailure
from app.tools.local.calendar import ListScheduleTool, RegisterPlanTool
from tests.agent_stubs import FULL_TRIP, TODAY, FakeLLM, World

SCRIPT = {"m": FULL_TRIP, "등록": {"intent": "REGISTER_CALENDAR", "intent_reason": "캘린더 등록 요청"},
          "3번 등록": {"intent": "REGISTER_CALENDAR", "intent_reason": "번호 지정", "plan_id": 3}}


class BrokenCalendar(LocalCalendarProvider):
    def list_events(self, start_at, end_at):
        raise ToolFailure(ErrorCode.AUTH_FAILED, "캘린더 인증 실패")


def setup(calendar_cls=LocalCalendarProvider):
    db = Database()
    plans, cal = PlanRepository(db), calendar_cls(db)
    world = World()
    agent = PlannerAgent(world.registry([ListScheduleTool(cal), RegisterPlanTool(cal, plans)]),
                         lambda: FakeLLM(SCRIPT), world.geocode, today=lambda: TODAY)
    agent.plan_lookup = plans.latest_for_session
    return agent, world, plans, cal


def tools(world):
    return [n for n, _ in world.calls]


def test_trip_checks_schedule_for_the_same_window_as_the_plan():
    agent, world, plans, cal = setup()
    r = agent.run("m", "S")
    assert r["status"] == "OK"
    step = next(e for e in r["timeline"] if e["type"] == "TOOL_CALL" and e["payload"]["tool"] == "list_schedule")
    assert step["payload"]["args"] == {"start_at": r["plan_draft"]["start_at"], "end_at": r["plan_draft"]["end_at"]}


def test_schedule_conflict_fails_all_candidates_without_replan():
    agent, world, plans, cal = setup()
    cal.create_event("회사 워크숍", "2026-11-19T13:00", "2026-11-19T18:00", 99, "")
    r = agent.run("m", "S")
    assert r["status"] == "NO_OPTION"
    assert r["replans"] == []                       # 캠핑장 · 충전소를 바꿔도 해결 안 됨 → 재계획 없음
    assert all(any("회사 워크숍" in why for why in e["reasons"]) for e in r["excluded"])
    assert any("날짜" in s for s in r["suggestions"])
    judge = next(e for e in r["timeline"] if e["type"] == "JUDGE_RESULT")
    assert all("SCHEDULE" in x["failed"] for x in judge["payload"]["results"])
    assert judge_turn(r["timeline"])["verdict"] == "OK"     # 계획에 있던 Tool만 호출


def test_schedule_lookup_failure_is_unjudged_not_ok():
    agent, world, plans, cal = setup(BrokenCalendar)
    r = agent.run("m", "S")
    assert r["status"] == "OK"
    assert all("SCHEDULE" in c["unjudged"] for c in r["recommendations"])
    assert any(f["tool"] == "list_schedule" and f["code"] == "AUTH_FAILED" for f in r["tool_failures"])


def test_register_uses_latest_plan_of_this_session_and_is_idempotent():
    agent, world, plans, cal = setup()
    r = agent.run("m", "S")
    saved, _ = plans.create(r["plan_draft"], session_id="S")
    first = agent.run("등록", "S")
    assert first["status"] == "OK" and first["calendar"]["already_registered"] is False
    assert first["calendar"]["event"]["title"] == saved["title"]
    assert first["calendar"]["event"]["start_at"] == saved["start_at"]
    call = next(e for e in first["timeline"] if e["type"] == "TOOL_CALL")
    assert call["payload"]["args"] == {"plan_id": saved["id"]}       # 계획 번호만 넘긴다
    assert judge_turn(first["timeline"])["verdict"] == "OK"
    again = agent.run("등록", "S")
    assert again["calendar"]["already_registered"] is True
    assert len(cal.list_events("2026-11-01T00:00", "2026-12-01T00:00")) == 1


def test_register_without_saved_plan_asks_first():
    agent, world, plans, cal = setup()
    r = agent.run("등록", "NEW")
    assert r["status"] == "NEED_INFO" and "계획" in r["missing"][0]
    assert tools(world) == []


def test_register_unknown_plan_number_reports_failure():
    agent, world, plans, cal = setup()
    r = agent.run("3번 등록", "S")
    assert r["status"] == "ERROR" and r["error_code"] == "NO_RESULT"
