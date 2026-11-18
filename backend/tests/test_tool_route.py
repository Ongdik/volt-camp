from app.tools.base import ErrorCode
from app.tools.external.route import GetRouteTool
from tests.conftest import FakeResponse, load_fixture

SEOUL = {"lat": 37.5663, "lon": 126.9779}
GAPYEONG = {"lat": 37.8315, "lon": 127.5097}


def test_real_route_distance_and_duration(fake_get):
    calls = fake_get(FakeResponse(load_fixture("kakao_directions.json")))
    result = GetRouteTool().invoke({"origin": SEOUL, "destination": GAPYEONG, "ref_id": "100361"})

    assert result.ok, result.error
    assert result.data["distance_km"] == 74.7
    assert result.data["duration_min"] == 100
    assert result.data["ref_id"] == "100361"
    assert result.data["via_waypoint"] is False
    # 카카오 좌표는 '경도,위도' 순서
    assert calls[0]["params"]["origin"] == "126.9779,37.5663"
    assert "waypoints" not in calls[0]["params"]
    assert any("가는 길과 같은" in n for n in result.meta.notes)


def test_waypoint_is_sent_as_lon_lat(fake_get):
    calls = fake_get(FakeResponse(load_fixture("kakao_directions.json")))
    GetRouteTool().invoke({"origin": SEOUL, "destination": GAPYEONG, "waypoint": {"lat": 37.7, "lon": 127.3}})

    assert calls[0]["params"]["waypoints"] == "127.3,37.7"


def test_no_route_comes_as_http_200(fake_get):
    fake_get(FakeResponse(load_fixture("kakao_directions_no_route.json")))
    result = GetRouteTool().invoke({"origin": SEOUL, "destination": {"lat": 35.0, "lon": 125.0}})

    assert result.error.code is ErrorCode.NO_ROUTE
    assert "도로를 탐색할 수 없음" in result.error.message


def test_auth_error(fake_get):
    fake_get(FakeResponse({"msg": "unauthorized"}, status_code=401))
    result = GetRouteTool().invoke({"origin": SEOUL, "destination": GAPYEONG})

    assert result.error.code is ErrorCode.AUTH_FAILED
    assert result.error.is_fatal
