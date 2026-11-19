"""verify_anomaly.py의 버그 주입 Agent가 실제 메인 Agent 흐름에서 의도한 이상 징후를 만드는지 (가짜 Tool)."""

from app.agent.graph import PlannerAgent
from app.db.anomalies import AnomalyRepository
from app.db.database import Database
from app.db.run_logs import RunLogRepository
from app.events.bus import EventBus
from app.logging_agent.agent import LoggingAgent
from scripts.verify_anomaly import DropStartRepo, HiddenStepAgent, RetryBugAgent
from tests.agent_stubs import FULL_TRIP, TODAY, FakeLLM, World, camp

SCRIPT = {"m": FULL_TRIP, "fact": {"intent": "FACT_QUERY", "intent_reason": "조회", "region": "가평",
                                   "fact_target": "campsites"},
          "hi": {"intent": "CHITCHAT", "intent_reason": "인사"},
          "far": {**FULL_TRIP, "soc_start_pct": 20, "min_return_pct": 60}}


def stack(repo_cls=RunLogRepository):
    db = Database()
    anomalies, diagnosed = AnomalyRepository(db), []
    bus = EventBus()
    LoggingAgent(repo_cls(db), anomalies, diagnoser=lambda ev, f: diagnosed.append(f) or "진단",
                 background=False).attach(bus)
    return bus, anomalies, diagnosed


def run(cls, message, world=None, repo_cls=RunLogRepository):
    bus, anomalies, diagnosed = stack(repo_cls)
    world = world or World()
    agent = cls(world.registry(), lambda: FakeLLM(SCRIPT), world.geocode, bus=bus, today=lambda: TODAY)
    agent.warning_lookup = anomalies.latest_warning
    r = agent.run(message, "S")
    [v] = anomalies.for_session("S")
    return r, v, diagnosed, agent


def test_normal_is_ok_without_llm():
    r, v, diagnosed, _ = run(PlannerAgent, "m")
    assert v["verdict"] == "OK" and diagnosed == []


def test_retry_bug_is_repeated_call_and_diagnosed():
    r, v, diagnosed, agent = run(RetryBugAgent, "m")
    assert [f["rule"] for f in v["findings"]] == ["REPEATED_CALL"] and len(diagnosed) == 1
    assert v["diagnosis_status"] == "DONE"
    follow = agent.run("hi", "S")        # 메인 Agent가 다음 턴에 직전 경고를 참조
    assert follow["previous_warning"]["findings"][0]["rule"] == "REPEATED_CALL"


def test_hidden_step_is_unplanned_tool():
    r, v, diagnosed, _ = run(HiddenStepAgent, "fact")
    assert [f["rule"] for f in v["findings"]] == ["UNPLANNED_TOOL"] and len(diagnosed) == 1


def test_replan_up_to_limit_is_excessive():
    world = World(campsites=[camp("c1", "A", False)], electric_sites=[camp("e1", "E", True)],
                  wide_sites=[camp("w1", "W", False)], distance_km=200, stations=[])
    r, v, diagnosed, _ = run(PlannerAgent, "far", world)
    assert len(r["replans"]) == 3
    assert "EXCESSIVE_REPLAN" in [f["rule"] for f in v["findings"]]


def test_lost_turn_start_is_undecidable_without_llm():
    r, v, diagnosed, _ = run(PlannerAgent, "hi", repo_cls=DropStartRepo)
    assert v["verdict"] == "UNDECIDABLE" and diagnosed == []
