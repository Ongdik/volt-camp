import pytest

from app.db.database import Database
from app.db.run_logs import RunLogRepository
from app.events.bus import EventBus, RunEmitter
from app.events.types import EventType
from app.logging_agent.agent import LoggingAgent


@pytest.fixture
def repo():
    return RunLogRepository(Database(":memory:"))


def test_publish_without_subscribers_is_fine():
    em = RunEmitter(EventBus(), "s1", "t1")
    em.emit(EventType.TURN_START, message="안녕")
    assert [e.seq for e in em.timeline] == [1]


def test_subscriber_exception_never_reaches_publisher():
    bus = EventBus()
    received = []

    def broken(event):
        raise RuntimeError("로깅 쪽 고장")

    bus.subscribe(broken)
    bus.subscribe(received.append)
    RunEmitter(bus, "s1", "t1").emit(EventType.NODE_ENTER, node="analyze")

    assert len(received) == 1           # 고장 난 구독자 뒤의 구독자도 받는다


def test_emitter_numbers_events_in_order():
    em = RunEmitter(None, "s1", "t1")
    for t in (EventType.TURN_START, EventType.NODE_ENTER, EventType.NODE_EXIT):
        em.emit(t)
    assert [e.seq for e in em.timeline] == [1, 2, 3]
    assert {e.session_id for e in em.timeline} == {"s1"}


def test_logging_agent_saves_raw_events_per_session(repo):
    bus = EventBus()
    LoggingAgent(repo).attach(bus)
    a, b = RunEmitter(bus, "A", "t1"), RunEmitter(bus, "B", "t1")
    a.emit(EventType.TURN_START, message="가평 차박")
    b.emit(EventType.TURN_START, message="안녕")
    a.emit(EventType.LLM_DECISION, node="analyze", kind="intent", decision="PLAN_TRIP", reason="날짜·지역·차량이 있음")
    a.emit(EventType.TURN_END, status="OK")

    events = repo.get_session("A")
    assert [e["type"] for e in events] == ["TURN_START", "LLM_DECISION", "TURN_END"]
    assert events[1]["payload"] == {"kind": "intent", "decision": "PLAN_TRIP", "reason": "날짜·지역·차량이 있음"}
    assert [e["type"] for e in repo.get_session("B")] == ["TURN_START"]   # 세션이 섞이지 않는다


def test_session_list_and_delete(repo):
    bus = EventBus()
    LoggingAgent(repo).attach(bus)
    em = RunEmitter(bus, "A", "t1")
    em.emit(EventType.TOOL_CALL, tool="get_route")
    em.emit(EventType.REPLAN, count=1)

    [summary] = repo.list_sessions()
    assert summary["session_id"] == "A" and summary["events"] == 2
    assert summary["replans"] == 1 and summary["tool_calls"] == 1

    assert repo.delete_session("A") == 2
    assert repo.get_session("A") == [] and repo.list_sessions() == []


def test_storage_failure_does_not_break_main_flow(repo):
    bus = EventBus()
    agent = LoggingAgent(repo).attach(bus)
    repo.db.close()                                  # 저장소를 망가뜨린다
    RunEmitter(bus, "A", "t1").emit(EventType.TURN_START)
    assert agent.failed == 1


def test_detached_logging_agent_stops_saving(repo):
    bus = EventBus()
    agent = LoggingAgent(repo).attach(bus)
    agent.detach()
    RunEmitter(bus, "A", "t1").emit(EventType.TURN_START)
    assert repo.get_session("A") == []


def test_run_log_repository_has_no_update():
    assert not [m for m in dir(RunLogRepository) if "update" in m or "edit" in m]
