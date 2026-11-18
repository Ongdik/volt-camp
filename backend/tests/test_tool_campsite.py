import pytest

from app.tools.base import ErrorCode
from app.tools.external.campsite import SearchCampsitesTool, electric_of
from tests.conftest import FakeResponse, load_fixture

GAPYEONG = {"lat": 37.8315, "lon": 127.5097}


@pytest.fixture
def tool():
    return SearchCampsitesTool()


def test_electric_from_facilities():
    assert electric_of("전기,무선인터넷,온수") is True
    assert electric_of("온수,물놀이장") is False
    assert electric_of("") is None          # 미기재는 '없음'이 아니라 '확인 불가'
    assert electric_of("   ") is None


def test_real_response_sorted_by_distance(tool, fake_get):
    calls = fake_get(FakeResponse(load_fixture("gocamping_location.json")))
    result = tool.invoke({**GAPYEONG, "radius_km": 20})

    assert result.ok, result.error
    cands = result.data["candidates"]
    assert len(cands) == 10
    assert [c["distance_km"] for c in cands] == sorted(c["distance_km"] for c in cands)
    assert sum(c["has_electric"] is True for c in cands) == 5
    first = cands[0]
    assert {"campsite_id", "name", "lat", "lon", "address", "has_electric", "reservation_url"} <= first.keys()
    # 요청 파라미터: 경도=mapX, 위도=mapY, 반경은 m
    assert calls[0]["params"]["mapX"] == 127.5097 and calls[0]["params"]["radius"] == 20000
    assert result.meta.actual["radius_km"] == 20


def test_require_electric_filters_and_explains(tool, fake_get):
    payload = load_fixture("gocamping_location.json")
    items = payload["response"]["body"]["items"]["item"]
    items[0]["sbrsCl"] = ""   # 한 곳은 부대시설 미기재로 바꿔 둔다
    unknown = sum(1 for it in items if not it["sbrsCl"].strip())
    fake_get(FakeResponse(payload))
    result = tool.invoke({**GAPYEONG, "require_electric": True})

    assert result.ok
    assert all(c["has_electric"] is True for c in result.data["candidates"])
    assert any(f"확인할 수 없는 {unknown}곳" in n for n in result.meta.notes)


def test_region_is_geocoded_first(tool, fake_get):
    keyword = {"documents": [{"place_name": "가평군청", "address_name": "경기 가평군 가평읍 읍내리",
                              "x": "127.5097", "y": "37.8315"}]}

    def route(url, params):
        if "dapi.kakao.com" in url:
            return FakeResponse(load_fixture("kakao_empty.json") if "address" in url else keyword)
        return FakeResponse(load_fixture("gocamping_location.json"))

    calls = fake_get(route)
    result = tool.invoke({"region": "가평"})

    assert result.ok
    assert [("kakao" in c["url"]) for c in calls] == [True, True, False]
    assert result.meta.actual["center"]["lat"] == 37.8315
    assert any("가평" in n for n in result.meta.notes)


def test_no_campsite_is_no_result(tool, fake_get):
    fake_get(FakeResponse(load_fixture("gocamping_empty.json")))
    result = tool.invoke(GAPYEONG)

    assert result.error.code is ErrorCode.NO_RESULT
    assert "10km" in result.error.message


def test_center_is_required(tool):
    assert tool.invoke({"radius_km": 5}).error.code is ErrorCode.INVALID_INPUT


def test_radius_upper_bound(tool):
    assert tool.invoke({**GAPYEONG, "radius_km": 50}).error.code is ErrorCode.INVALID_INPUT
