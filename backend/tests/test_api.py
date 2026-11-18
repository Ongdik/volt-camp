import pytest
from fastapi.testclient import TestClient

from app.agent.graph import PlannerAgent
from app.db.database import Database
from app.events.bus import EventBus
from app.main import create_app
from tests.agent_stubs import FULL_TRIP, TODAY, FakeLLM, World

SCRIPT = {
    "계획": FULL_TRIP,
    "저장": {"intent": "CONFIRM_PLAN", "intent_reason": "확정"},
    "안녕": {"intent": "CHITCHAT", "intent_reason": "인사"},
}


@pytest.fixture
def client():
    world, bus = World(), EventBus()
    planner = PlannerAgent(world.registry(), lambda: FakeLLM(SCRIPT), world.geocode, bus=bus, today=lambda: TODAY)
    with TestClient(create_app(planner=planner, db=Database(":memory:"), bus=bus)) as c:
        yield c


def draft(start="2026-11-19T10:00", end="2026-11-20T14:00", title="가평 차박"):
    return {"title": title, "campsite": {"campsite_id": "c1", "name": "펫밀리 캠프"}, "start_at": start, "end_at": end,
            "vehicle": {"capacity_kwh": 77.4, "soc_start_pct": 80}, "devices": [{"name": "전기장판", "hours": 8}]}


def test_chat_plan_turn(client):
    r = client.post("/api/chat", json={"message": "계획", "session_id": "S1"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "OK" and body["session_id"] == "S1"
    assert body["recommendations"] and body["plan_draft"]
    assert any(e["type"] == "TOOL_CALL" for e in body["timeline"])


def test_chat_confirm_saves_plan_and_reports_overlap(client):
    client.post("/api/plans", json=draft(title="기존 계획"))
    client.post("/api/chat", json={"message": "계획", "session_id": "S"})
    body = client.post("/api/chat", json={"message": "저장", "session_id": "S"}).json()

    assert body["action"] == "SAVE_PLAN" and body["saved_plan"]["id"]
    assert [o["title"] for o in body["overlaps"]] == ["기존 계획"]
    assert "기존 계획" in body["answer"]
    assert len(client.get("/api/plans").json()) == 2


def test_chat_input_validation(client):
    r = client.post("/api/chat", json={"message": ""})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_INPUT"


def test_logs_are_saved_by_logging_agent(client):
    client.post("/api/chat", json={"message": "안녕", "session_id": "L1"})
    sessions = client.get("/api/logs").json()
    assert sessions[0]["session_id"] == "L1" and sessions[0]["turns"] == 1
    events = client.get("/api/logs/L1").json()
    assert events[0]["type"] == "TURN_START" and events[-1]["type"] == "TURN_END"
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)


def test_logs_delete_and_no_update(client):
    client.post("/api/chat", json={"message": "안녕", "session_id": "L2"})
    assert client.put("/api/logs/L2", json={}).status_code == 405
    assert client.patch("/api/logs/L2", json={}).status_code == 405
    assert client.delete("/api/logs/L2").status_code == 204
    assert client.get("/api/logs/L2").status_code == 404


def test_plans_crud(client):
    created = client.post("/api/plans", json=draft())
    assert created.status_code == 201 and created.json()["overlaps"] == []
    pid = created.json()["plan"]["id"]
    assert client.get(f"/api/plans/{pid}").json()["title"] == "가평 차박"

    second = client.post("/api/plans", json=draft("2026-11-20T09:00", "2026-11-21T09:00", "겹침")).json()
    assert [o["id"] for o in second["overlaps"]] == [pid]

    updated = client.put(f"/api/plans/{pid}", json={"memo": "장작 챙기기", "start_at": "2026-11-19T12:00"}).json()
    assert updated["plan"]["memo"] == "장작 챙기기" and updated["plan"]["start_at"] == "2026-11-19T12:00"

    assert client.delete(f"/api/plans/{pid}").status_code == 204
    r = client.get(f"/api/plans/{pid}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "PLAN_NOT_FOUND"


def test_plan_invalid_period(client):
    r = client.post("/api/plans", json=draft("2026-11-20T10:00", "2026-11-19T10:00"))
    assert r.status_code == 400 and "귀가" in r.json()["error"]["message"]


def test_health_reports_only_booleans(client, monkeypatch):
    body = client.get("/api/health").json()
    assert body == {"llm": False, "data_go_kr": True, "kakao": True}   # 테스트 설정: LLM 키 없음
    assert "test-data-key" not in str(body)


def test_openapi_lists_all_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/api/chat", "/api/plans", "/api/plans/{plan_id}", "/api/logs", "/api/logs/{session_id}",
            "/api/health"} <= set(paths)
