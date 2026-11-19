"""캘린더 Tool: 로컬 구현체로 동작 확인, Google 구현체로 바꿔도 입출력 형식이 같은지."""

import pytest

from app.calendars import GoogleCalendarProvider, LocalCalendarProvider
from app.db.database import Database
from app.db.plans import PlanRepository
from app.tools.local.calendar import ListScheduleTool, RegisterPlanTool

DRAFT = {"title": "가평 첫 차박", "campsite": {"name": "자라섬 캠핑장", "address": "가평"}, "origin": "서울시청",
         "start_at": "2026-11-20T10:00", "end_at": "2026-11-21T14:00", "vehicle": {"capacity_kwh": 77.4},
         "devices": [], "expected_soc": {"arrive": 0.58, "after_camp": 0.58, "return": 0.33},
         "charge_stop": {"station": {"name": "가평터미널"}, "charge_kwh": 2.9, "charge_minutes": 1}}


class FakeRequest:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeGoogleEvents:
    """Google Calendar events() 리소스를 흉내 낸다 (요청 인자를 기록)."""

    def __init__(self):
        self.items, self.calls = [], []

    def list(self, **kw):
        self.calls.append(("list", kw))
        if "privateExtendedProperty" in kw:
            key = kw["privateExtendedProperty"].split("=")[1]
            return FakeRequest(lambda: {"items": [i for i in self.items
                                                  if i["extendedProperties"]["private"]["voltcamp_plan_id"] == key]})
        return FakeRequest(lambda: {"items": self.items + [
            {"id": "g-all", "summary": "가족 행사", "start": {"date": "2026-11-21"}, "end": {"date": "2026-11-22"}}]})

    def insert(self, calendarId, body):
        self.calls.append(("insert", body))
        item = {"id": f"g-{len(self.items) + 1}", "summary": body["summary"], "start": body["start"],
                "end": body["end"], "extendedProperties": body["extendedProperties"]}
        self.items.append(item)
        return FakeRequest(lambda: item)


class FakeGoogleService:
    def __init__(self):
        self._events = FakeGoogleEvents()

    def events(self):
        return self._events


@pytest.fixture
def plans():
    return PlanRepository(Database())


def providers(plans):
    return {"local": LocalCalendarProvider(plans.db),
            "google": GoogleCalendarProvider("primary", service=FakeGoogleService())}


@pytest.mark.parametrize("kind", ["local", "google"])
def test_register_reads_title_and_time_from_plan_store(plans, kind):
    cal = providers(plans)[kind]
    plan, _ = plans.create(DRAFT)
    r = RegisterPlanTool(cal, plans).invoke({"plan_id": plan["id"]})
    assert r.ok, r.error
    ev = r.data["event"]
    assert (ev["title"], ev["start_at"], ev["end_at"], ev["plan_id"]) == (
        "가평 첫 차박", "2026-11-20T10:00", "2026-11-21T14:00", plan["id"])
    assert r.data["already_registered"] is False and r.data["provider"] == kind
    # 같은 계획을 다시 등록하면 기존 일정
    again = RegisterPlanTool(cal, plans).invoke({"plan_id": plan["id"]})
    assert again.data["already_registered"] is True and again.data["event"]["event_id"] == ev["event_id"]


@pytest.mark.parametrize("kind", ["local", "google"])
def test_list_schedule_same_shape_for_both_providers(plans, kind):
    cal = providers(plans)[kind]
    plan, _ = plans.create(DRAFT)
    RegisterPlanTool(cal, plans).invoke({"plan_id": plan["id"]})
    r = ListScheduleTool(cal).invoke({"start_at": "2026-11-20T00:00", "end_at": "2026-11-22T00:00"})
    assert r.ok
    assert set(r.data) == {"provider", "range", "events", "count"}
    assert set(r.data["events"][0]) == {"event_id", "title", "start_at", "end_at", "plan_id", "all_day"}
    assert r.data["events"][0]["title"] == "가평 첫 차박"


def test_register_accepts_only_plan_id(plans):
    tool = RegisterPlanTool(LocalCalendarProvider(plans.db), plans)
    assert set(tool.input_model.model_json_schema()["properties"]) == {"plan_id"}
    assert tool.invoke({"plan_id": 0}).error.code == "INVALID_INPUT"
    assert tool.invoke({"plan_id": 99}).error.code == "NO_RESULT"


def test_local_list_is_half_open_overlap(plans):
    cal = LocalCalendarProvider(plans.db)
    cal.create_event("앞 일정", "2026-11-19T18:00", "2026-11-20T10:00", 1, "")
    cal.create_event("겹치는 일정", "2026-11-21T09:00", "2026-11-21T11:00", 2, "")
    titles = [e.title for e in cal.list_events("2026-11-20T10:00", "2026-11-21T14:00")]
    assert titles == ["겹치는 일정"]   # 끝 시각 = 출발 시각처럼 맞닿기만 하면 겹침 아님


def test_list_schedule_validates_period(plans):
    tool = ListScheduleTool(LocalCalendarProvider(plans.db))
    assert tool.invoke({"start_at": "2026-11-21T00:00", "end_at": "2026-11-20T00:00"}).error.code == "INVALID_INPUT"
    assert tool.invoke({"start_at": "내일", "end_at": "모레"}).error.code == "INVALID_INPUT"
    ok = tool.invoke({"start_at": "2026-11-20T00:00", "end_at": "2026-11-21T00:00"})
    assert ok.ok and ok.data["events"] == []   # 일정 없음은 실패가 아니다


def test_google_all_day_event_and_timezone(plans):
    svc = FakeGoogleService()
    cal = GoogleCalendarProvider("primary", service=svc)
    events = cal.list_events("2026-11-20T00:00", "2026-11-23T00:00")
    allday = next(e for e in events if e.title == "가족 행사")
    assert (allday.start_at, allday.end_at, allday.all_day) == ("2026-11-21T00:00", "2026-11-22T00:00", True)
    _, kw = svc.events().calls[0]
    assert kw["timeMin"] == "2026-11-20T00:00:00+09:00"


def test_google_errors_become_tool_failures(plans):
    class Resp:
        status = 401

    class Boom:
        def execute(self):
            e = Exception("unauthorized")
            e.resp = Resp()
            raise e

    class Events:
        def list(self, **kw):
            return Boom()

    class Svc:
        def events(self):
            return Events()

    r = ListScheduleTool(GoogleCalendarProvider("primary", service=Svc())).invoke(
        {"start_at": "2026-11-20T00:00", "end_at": "2026-11-21T00:00"})
    assert r.error.code == "AUTH_FAILED"


def test_google_without_token_is_missing_key(plans, tmp_path):
    r = ListScheduleTool(GoogleCalendarProvider("primary", token_file=str(tmp_path / "none.json"))).invoke(
        {"start_at": "2026-11-20T00:00", "end_at": "2026-11-21T00:00"})
    assert r.error.code == "MISSING_API_KEY"
