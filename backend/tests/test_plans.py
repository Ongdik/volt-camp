import pytest

from app.db.database import Database
from app.db.plans import PlanRepository


def draft(title="가평 차박", start="2026-11-19T10:00", end="2026-11-20T14:00"):
    return {"title": title, "campsite": {"campsite_id": "c1", "name": "펫밀리 캠프"}, "origin": "서울시청",
            "start_at": start, "end_at": end, "vehicle": {"capacity_kwh": 77.4, "soc_start_pct": 80},
            "devices": [{"name": "전기장판", "hours": 8}], "charge_stop": None,
            "expected_soc": {"arrive": 0.58, "after_camp": 0.55, "return": 0.33}, "evidence": {"distance_km": 74.7}}


@pytest.fixture
def repo():
    return PlanRepository(Database(":memory:"))


def test_create_and_read(repo):
    plan, overlaps = repo.create(draft(), session_id="s1")
    assert overlaps == []
    assert plan["id"] and plan["campsite"]["name"] == "펫밀리 캠프" and plan["expected_soc"]["return"] == 0.33
    assert plan["created_at"] and plan["session_id"] == "s1"
    assert repo.get(plan["id"]) == plan


def test_overlap_is_reported_but_saved(repo):
    first, _ = repo.create(draft("A"))
    second, overlaps = repo.create(draft("B", "2026-11-20T09:00", "2026-11-21T12:00"))
    assert second["id"] and [o["id"] for o in overlaps] == [first["id"]]
    assert len(repo.list()) == 2


def test_touching_periods_do_not_overlap(repo):
    repo.create(draft("A"))
    _, overlaps = repo.create(draft("B", "2026-11-20T14:00", "2026-11-21T14:00"))
    assert overlaps == []


def test_list_is_ordered_by_start(repo):
    repo.create(draft("늦은", "2026-12-01T10:00", "2026-12-02T10:00"))
    repo.create(draft("이른", "2026-11-19T10:00", "2026-11-20T10:00"))
    assert [p["title"] for p in repo.list()] == ["이른", "늦은"]


def test_update_checks_overlap_excluding_itself(repo):
    a, _ = repo.create(draft("A"))
    b, _ = repo.create(draft("B", "2026-11-25T10:00", "2026-11-26T10:00"))
    updated, overlaps = repo.update(b["id"], {"start_at": "2026-11-20T08:00", "memo": "하루 당김",
                                              "devices": [{"name": "전기장판", "hours": 6}]})
    assert updated["memo"] == "하루 당김" and updated["devices"][0]["hours"] == 6
    assert [o["id"] for o in overlaps] == [a["id"]]
    _, self_only = repo.update(a["id"], {"memo": "그대로"})
    assert [o["id"] for o in self_only] == [b["id"]]       # 자기 자신은 빠진다


def test_update_ignores_non_editable_fields(repo):
    a, _ = repo.create(draft())
    updated, _ = repo.update(a["id"], {"campsite": {"name": "바꿈"}, "title": "새 제목"})
    assert updated["campsite"]["name"] == "펫밀리 캠프" and updated["title"] == "새 제목"


def test_invalid_period_rejected(repo):
    with pytest.raises(ValueError):
        repo.create(draft(start="2026-11-20T10:00", end="2026-11-19T10:00"))
    a, _ = repo.create(draft())
    with pytest.raises(ValueError):
        repo.update(a["id"], {"end_at": "2026-11-18T10:00"})


def test_delete_and_missing(repo):
    a, _ = repo.create(draft())
    assert repo.delete(a["id"]) is True and repo.get(a["id"]) is None
    assert repo.delete(a["id"]) is False and repo.update(999, {"memo": "x"}) is None
