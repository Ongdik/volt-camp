import pytest

from app.db.anomalies import AnomalyRepository
from app.db.database import Database
from app.db.run_logs import RunLogRepository
from app.events.bus import EventBus, RunEmitter
from app.events.types import Event
from app.events.types import EventType as T
from app.logging_agent.agent import LoggingAgent
from app.logging_agent.anomaly import judge_turn


def turn(*steps, start=True, end=True):
    """(type, payload) 목록으로 한 턴의 이벤트를 만든다."""
    em = RunEmitter(None, "S", "t1")
    if start:
        em.emit(T.TURN_START)
    em.emit(T.LLM_DECISION, kind="plan", decision=["search_campsites", "get_weather"])
    for type_, payload in steps:
        em.emit(type_, **payload)
    if end:
        em.emit(T.TURN_END)
    return [e.model_dump(mode="json") for e in em.timeline]


def call(step, tool, args=None, cache=False):
    return [(T.TOOL_CALL, {"step_id": step, "tool": tool, "args": args or {}, "from_cache": cache}),
            (T.TOOL_RESULT, {"step_id": step, "tool": tool, "ok": True})]


def rules(result):
    return [f["rule"] for f in result["findings"]]


def test_normal_turn_is_ok():
    r = judge_turn(turn(*call("s1", "search_campsites"), *call("s2", "get_weather"), *call("route:c1", "get_route")))
    assert r["verdict"] == "OK" and r["findings"] == []


def test_repeated_call_but_cache_is_fine():
    same = {"lat": 1}
    r = judge_turn(turn(*call("a", "get_route", same), *call("b", "get_route", same)))
    assert rules(r) == ["REPEATED_CALL"] and r["findings"][0]["evidence_seq"]
    ok = judge_turn(turn(*call("a", "get_route", same), *call("b", "get_route", same, cache=True)))
    assert ok["verdict"] == "OK"


def test_excessive_replan():
    steps = [(T.REPLAN, {"count": i, "strategy": "EXPAND_RADIUS"}) for i in (1, 2, 3)]
    assert rules(judge_turn(turn(*steps))) == ["EXCESSIVE_REPLAN"]


def test_unplanned_tool_and_replan_allows_it():
    assert rules(judge_turn(turn(*call("x", "search_chargers")))) == ["UNPLANNED_TOOL"]
    allowed = turn((T.REPLAN, {"count": 1, "strategy": "ADD_CHARGE_STOP"}), *call("x", "search_chargers"))
    assert judge_turn(allowed)["verdict"] == "OK"


def test_missing_result():
    r = judge_turn(turn((T.TOOL_CALL, {"step_id": "s1", "tool": "search_campsites", "args": {}})))
    assert rules(r) == ["MISSING_RESULT"]


def test_without_turn_boundaries_is_undecidable_not_ok():
    assert judge_turn(turn(end=False))["verdict"] == "UNDECIDABLE"
    assert judge_turn([])["verdict"] == "UNDECIDABLE"


@pytest.fixture
def stack():
    db = Database(":memory:")
    return RunLogRepository(db), AnomalyRepository(db)


def publish(bus, steps):
    em = RunEmitter(bus, "S", "t1")
    em.emit(T.TURN_START)
    em.emit(T.LLM_DECISION, kind="plan", decision=["search_campsites"])
    for type_, payload in steps:
        em.emit(type_, **payload)
    em.emit(T.TURN_END)


def test_only_warn_turns_are_diagnosed(stack):
    logs, anomalies = stack
    calls = []
    bus = EventBus()
    agent = LoggingAgent(logs, anomalies, diagnoser=lambda ev, f: calls.append(f) or "계획 규칙 확인 필요",
                         background=False).attach(bus)
    publish(bus, call("s1", "search_campsites"))
    assert agent.diagnose_calls == 0 and anomalies.for_session("S")[0]["verdict"] == "OK"

    bus2 = EventBus()
    LoggingAgent(logs, anomalies, diagnoser=lambda ev, f: "검색 Tool을 같은 인자로 두 번 불렀습니다.",
                 background=False).attach(bus2)
    em = RunEmitter(bus2, "S2", "t2")
    em.emit(T.TURN_START)
    em.emit(T.LLM_DECISION, kind="plan", decision=["search_campsites"])
    for t_, p in call("a", "search_campsites", {"x": 1}) + call("b", "search_campsites", {"x": 1}):
        em.emit(t_, **p)
    em.emit(T.TURN_END)
    [a] = anomalies.for_session("S2")
    assert a["verdict"] == "WARN" and a["diagnosis_status"] == "DONE" and "두 번" in a["diagnosis"]
    assert [f["rule"] for f in a["findings"]] == ["REPEATED_CALL"]      # 판정과 진단은 다른 칸
    assert anomalies.latest_warning("S2")["turn_id"] == "t2"


def test_diagnosis_failure_keeps_verdict(stack):
    logs, anomalies = stack
    bus = EventBus()

    def broken(ev, f):
        raise RuntimeError("LLM down")

    LoggingAgent(logs, anomalies, diagnoser=broken, background=False).attach(bus)
    publish(bus, call("x", "search_chargers"))
    [a] = anomalies.for_session("S")
    assert a["verdict"] == "WARN" and a["diagnosis_status"] == "FAILED" and a["diagnosis"] is None
    assert logs.get_session("S")                                      # 로그도 그대로


def test_undecidable_reason_is_saved(stack):
    logs, anomalies = stack
    LoggingAgent(logs, anomalies).handle(Event(session_id="U", turn_id="tu", seq=1, type=T.TURN_END, payload={}))
    [a] = anomalies.for_session("U")
    assert a["verdict"] == "UNDECIDABLE" and "판정할 수 없습니다" in a["reason"]


def test_old_db_file_gets_reason_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE anomalies (id INTEGER PRIMARY KEY, session_id TEXT, turn_id TEXT UNIQUE, verdict TEXT,"
                " findings_json TEXT, diagnosis TEXT, diagnosis_status TEXT, created_at TEXT)")
    con.commit()
    con.close()
    repo = AnomalyRepository(Database(str(path)))
    repo.save_verdict("S", "t", "OK", [], "")
    assert repo.for_session("S")[0]["reason"] == ""
