"""메인 Agent 대표 시나리오 (가짜 LLM · 가짜 Tool). 수치는 test_energy.py와 같은 계산식."""

import pytest

from app.agent.graph import PlannerAgent
from app.events.bus import EventBus
from app.events.types import EventType
from app.tools.base import ErrorCode, ToolFailure
from tests.agent_stubs import FULL_TRIP, MILD, RAINY, TODAY, FakeLLM, World, camp


def make_agent(world: World, script: dict, bus=None, max_replan=3):
    llm = FakeLLM(script)
    agent = PlannerAgent(world.registry(), lambda: llm, world.geocode, bus=bus, max_replan=max_replan,
                         today=lambda: TODAY)
    return agent, llm


def tools_called(world: World) -> list[str]:
    return [name for name, _ in world.calls]


def events(result, type_: EventType):
    return [e for e in result["timeline"] if e["type"] == type_.value]


# ---------------------------------------------------------------- S1 한 번에 충족

def test_s1_satisfied_at_once():
    world = World()
    agent, llm = make_agent(world, {"m": FULL_TRIP})
    r = agent.run("m", "S1")

    assert r["status"] == "OK"
    assert r["replans"] == []
    names = [c["campsite"]["name"] for c in r["recommendations"]]
    assert set(names) == {"펫밀리 캠프", "축령산"}
    # 전기 사이트(펫밀리)는 캠핑 소비를 차 배터리에서 빼지 않아 귀가 잔량이 더 높다 → 먼저
    assert names[0] == "펫밀리 캠프"
    socs = {c["campsite"]["name"]: c["soc"] for c in r["recommendations"]}
    assert socs["축령산"]["return"] == pytest.approx(0.333, abs=0.001)
    assert tools_called(world) == ["search_campsites", "get_weather", "get_route", "get_route"]
    assert r["plan_draft"]["campsite"]["name"] == "펫밀리 캠프"
    assert r["plan_draft"]["start_at"] == "2026-11-19T10:00" and r["plan_draft"]["end_at"] == "2026-11-20T14:00"
    assert any("기본값" in a for a in r["assumptions"])
    assert r["notices"] and "사용설명서" in r["notices"][0]
    assert llm.analyze_calls == 1 and [k for k, _ in llm.write_calls] == ["plan"]


# ---------------------------------------------------------------- S2 배터리 부족 → 충전 경유

def trip(**kw):
    return {**FULL_TRIP, **kw}


ENERGY_SHORT = trip(soc_start_pct=45, devices=[{"name": "전기히터", "hours": 6}, {"name": "전기장판", "hours": 8}])


def test_s2_replan_with_charge_stop():
    world = World(campsites=[camp("c1", "A캠핑", None), camp("c2", "B캠핑", False, lat=37.75, lon=127.45)])
    agent, _ = make_agent(world, {"m": ENERGY_SHORT})
    r = agent.run("m", "S2")

    assert r["status"] == "OK"
    assert [p["strategy"] for p in r["replans"]] == ["ADD_CHARGE_STOP"]
    rec = r["recommendations"][0]
    cs = rec["charge_stop"]
    assert cs["station"]["name"] == "가평휴게소 급속"
    assert cs["charge_kwh"] == pytest.approx(34.6, abs=0.1)        # 부족 32.6 + 여유 2
    assert cs["charge_minutes"] == 27                               # 34.6 / (100 × 0.85) × 60 × 1.1
    assert rec["soc"]["return"] >= 0.30
    # 재계획 때 이미 받은 결과(캠핑장 · 직행 경로 · 날씨)는 다시 부르지 않는다
    called = tools_called(world)
    assert called.count("search_campsites") == 1 and called.count("get_weather") == 1
    direct = [a for n, a in world.calls if n == "get_route" and not a.get("waypoint")]
    assert len(direct) == 2
    assert called.count("search_chargers") == 2
    assert len(events(r, EventType.REPLAN)) == 1


def test_s2_latest_arrival_is_back_calculated():
    world = World(campsites=[camp("c1", "A캠핑", None)])
    agent, _ = make_agent(world, {"m": {**ENERGY_SHORT, "arrival_time": "17:00"}})
    cs = agent.run("m")["recommendations"][0]["charge_stop"]
    # 17:00 − 충전 27분 − 충전소→캠핑장 30분 = 16:03
    assert cs["latest_arrival"] == "16:03"


def test_charging_refused_skips_charge_strategy():
    world = World(campsites=[camp("c1", "A캠핑", None)], electric_sites=[camp("e1", "전기캠핑", True)])
    agent, _ = make_agent(world, {"m": {**ENERGY_SHORT, "allow_charging": False}})
    r = agent.run("m")
    assert r["replans"][0]["strategy"] == "USE_ELECTRIC_SITE"
    assert "search_chargers" not in tools_called(world)


# ---------------------------------------------------------------- S3 비 예보 → 전기 사이트

def test_s3_rain_switches_to_electric_site():
    world = World(weather=RAINY, campsites=[camp("c1", "A캠핑", None), camp("c2", "B캠핑", False)],
                  electric_sites=[camp("e1", "전기캠핑", True)])
    agent, _ = make_agent(world, {"m": trip(min_return_pct=20, devices=[{"name": "전기히터", "hours": 2}])})
    r = agent.run("m")

    assert r["status"] == "OK"
    assert [p["strategy"] for p in r["replans"]] == ["USE_ELECTRIC_SITE"]
    assert r["recommendations"][0]["campsite"]["name"] == "전기캠핑"
    assert {e["campsite_name"] for e in r["excluded"]} == {"A캠핑", "B캠핑"}
    assert all(any("비" in reason for reason in e["reasons"]) for e in r["excluded"])


# ---------------------------------------------------------------- S4 재계획 상한

def test_s4_stops_at_replan_limit():
    world = World(campsites=[camp("c1", "A캠핑", None)], electric_sites=[camp("e1", "전기캠핑", True)])
    agent, _ = make_agent(world, {"m": trip(soc_start_pct=25, min_return_pct=50,
                                            devices=[{"name": "전기히터", "hours": 10}])})
    r = agent.run("m")

    assert r["status"] == "LIMIT_REACHED"
    assert [p["strategy"] for p in r["replans"]] == ["ADD_CHARGE_STOP", "USE_ELECTRIC_SITE", "EXPAND_RADIUS"]
    assert r["recommendations"] == [] and r["plan_draft"] is None
    assert r["suggestions"]


def test_power_overload_is_not_replanned():
    world = World()
    devices = [{"name": "전기히터", "hours": 1}, {"name": "전기포트", "hours": 0.2}, {"name": "인덕션", "hours": 0.5}]
    world.campsites = [camp("c1", "A캠핑", False)]
    agent, _ = make_agent(world, {"m": trip(min_return_pct=10, devices=devices)})
    r = agent.run("m")

    assert r["replans"] == [] and r["status"] == "NO_OPTION"
    assert any("동시에" in s for s in r["suggestions"])


# ---------------------------------------------------------------- S5 Tool 실패

def test_s5_weather_failure_does_not_stop_service():
    world = World(fail={"get_weather": ErrorCode.UPSTREAM_ERROR})
    agent, _ = make_agent(world, {"m": FULL_TRIP})
    r = agent.run("m")

    assert r["status"] == "OK"
    assert all(rec["unjudged"] == ["RAIN"] for rec in r["recommendations"])
    assert r["tool_failures"] == [{"tool": "get_weather", "code": "UPSTREAM_ERROR", "message": "get_weather 실패"}]


def test_s5_missing_key_is_reported():
    world = World(campsites=[camp("c1", "A캠핑", None)], fail={"search_chargers": ErrorCode.MISSING_API_KEY})
    agent, _ = make_agent(world, {"m": ENERGY_SHORT})
    r = agent.run("m")

    assert r["status"] in ("NO_OPTION", "LIMIT_REACHED")
    assert any(f["code"] == "MISSING_API_KEY" for f in r["tool_failures"])


def test_fatal_campsite_failure_stops_replanning():
    world = World(fail={"search_campsites": ErrorCode.AUTH_FAILED})
    agent, _ = make_agent(world, {"m": FULL_TRIP})
    r = agent.run("m")
    assert r["replans"] == []
    assert tools_called(world) == ["search_campsites", "get_weather"]


# ---------------------------------------------------------------- S6 Tool 불필요 / S7 되묻기

def test_s6_chitchat_calls_no_tool():
    world = World()
    agent, llm = make_agent(world, {"안녕": {"intent": "CHITCHAT", "intent_reason": "인사"}})
    r = agent.run("안녕")

    assert r["status"] == "OK" and r["answer"]
    assert world.calls == []
    nodes = [e["node"] for e in events(r, EventType.NODE_ENTER)]
    assert nodes == ["analyze", "respond"]                       # 계획 단계를 건너뛴다


def test_s7_asks_before_calling_tools():
    world = World()
    agent, _ = make_agent(world, {"m": {"intent": "PLAN_TRIP", "intent_reason": "차박 질문", "region": "가평"}})
    r = agent.run("m")

    assert r["status"] == "NEED_INFO"
    assert world.calls == []
    assert {"출발 날짜", "출발지", "배터리 용량(kWh)", "현재 배터리 잔량(%)", "캠핑장에서 쓸 기기"} <= set(r["missing"])


def test_unknown_device_power_is_asked():
    agent, _ = make_agent(World(), {"m": trip(devices=[{"name": "빔프로젝터", "hours": 2}, {"name": "제빙기", "hours": 1}])})
    assert agent.run("m")["missing"] == ["제빙기 소비전력(W)"]


def test_unknown_place_is_asked():
    agent, _ = make_agent(World(), {"m": trip(region="없는동네")})
    r = agent.run("m")
    assert r["status"] == "NEED_INFO" and "없는동네" in r["missing"][0]


# ---------------------------------------------------------------- S8 후속 요청 · 저장

def test_s8_follow_up_changes_only_region():
    world = World()
    script = {"first": FULL_TRIP,
              "같은 날 홍천으로": {"intent": "MODIFY", "intent_reason": "지역만 변경", "region": "홍천"}}
    agent, _ = make_agent(world, script)
    agent.run("first", "S8")
    r = agent.run("같은 날 홍천으로", "S8")

    assert r["intent"] == "PLAN_TRIP" and r["changed"] == ["region"]
    cond = r["conditions"]
    assert cond["region"] == "홍천"
    assert cond["start_date"] == "2026-11-19" and cond["capacity_kwh"] == 77.4
    assert cond["devices"] == [{"name": "전기장판", "hours": 8.0}]


def test_confirm_returns_save_action():
    agent, _ = make_agent(World(), {"m": FULL_TRIP, "저장": {"intent": "CONFIRM_PLAN", "intent_reason": "확정"}})
    agent.run("m", "S")
    r = agent.run("저장", "S")
    assert r["action"] == "SAVE_PLAN" and r["plan_draft"]["campsite"]["name"] == "펫밀리 캠프"


def test_confirm_without_plan_asks():
    agent, _ = make_agent(World(), {"저장": {"intent": "CONFIRM_PLAN", "intent_reason": "확정"}})
    r = agent.run("저장")
    assert r["status"] == "NEED_INFO" and r["action"] is None


def test_fact_query_calls_one_tool():
    world = World()
    agent, llm = make_agent(world, {"q": {"intent": "FACT_QUERY", "intent_reason": "충전소만 질문",
                                          "region": "가평", "fact_target": "chargers"}})
    r = agent.run("q")
    assert tools_called(world) == ["search_chargers"]
    assert r["status"] == "OK" and llm.write_calls[0][0] == "fact"


# ---------------------------------------------------------------- 이벤트 · 분리

def test_events_cover_steps_tools_and_llm_decisions():
    world = World()
    agent, _ = make_agent(world, {"m": FULL_TRIP})
    r = agent.run("m")

    kinds = {e["type"] for e in r["timeline"]}
    assert {"TURN_START", "NODE_ENTER", "NODE_EXIT", "LLM_DECISION", "TOOL_CALL", "TOOL_RESULT",
            "JUDGE_RESULT", "TURN_END"} <= kinds
    intent = next(e for e in events(r, EventType.LLM_DECISION) if e["payload"]["kind"] == "intent")
    assert intent["payload"]["decision"] == "PLAN_TRIP" and intent["payload"]["reason"]
    assert len(events(r, EventType.TOOL_CALL)) == len(events(r, EventType.TOOL_RESULT)) == 4
    assert [e["seq"] for e in r["timeline"]] == list(range(1, len(r["timeline"]) + 1))


def test_broken_logging_does_not_break_agent():
    bus = EventBus()
    bus.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("로깅 고장")))
    agent, _ = make_agent(World(), {"m": FULL_TRIP}, bus=bus)
    assert agent.run("m")["status"] == "OK"


def test_llm_failure_is_reported_not_raised():
    class Broken(FakeLLM):
        def analyze(self, *a, **k):
            raise RuntimeError("timeout")

    world = World()
    agent = PlannerAgent(world.registry(), lambda: Broken({}), world.geocode, today=lambda: TODAY)
    r = agent.run("m")
    assert r["status"] == "ERROR" and r["error_code"] == "LLM_ERROR"
    assert world.calls == []


def test_same_args_are_served_from_cache():
    # 두 후보 좌표가 같으면 충전소 검색 인자도 같아 두 번째는 캐시에서 꺼낸다
    world = World(campsites=[camp("c1", "A캠핑", None), camp("c2", "B캠핑", False)])
    agent, _ = make_agent(world, {"m": ENERGY_SHORT})
    r = agent.run("m")
    assert tools_called(world).count("search_chargers") == 1
    cached = [e for e in events(r, EventType.TOOL_CALL) if e["payload"]["from_cache"]]
    assert [e["payload"]["tool"] for e in cached] == ["search_chargers"]


def test_llm_quota_failure_tells_the_reason():
    class OutOfTokens(FakeLLM):
        def analyze(self, *a, **k):
            raise ToolFailure(ErrorCode.QUOTA_EXCEEDED, "LLM 사용량(토큰)이 모두 소진됐어요")

    world = World()
    agent = PlannerAgent(world.registry(), lambda: OutOfTokens({}), world.geocode, today=lambda: TODAY)
    r = agent.run("m")
    assert r["status"] == "ERROR" and r["error_code"] == "QUOTA_EXCEEDED"
    assert "소진" in r["answer"]
    assert world.calls == []
