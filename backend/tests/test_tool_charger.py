import pytest

from app.tools.base import ErrorCode
from app.tools.external.charger import SearchChargersTool
from tests.conftest import FakeResponse, load_fixture

GAPYEONG = {"lat": 37.8315, "lon": 127.5097}


def route_real(url, params):
    if "coord2regioncode" in url:
        return FakeResponse(load_fixture("kakao_region_gapyeong.json"))
    return FakeResponse(load_fixture("ev_charger_gapyeong.json"))


def test_region_code_becomes_zcode_and_zscode(fake_get):
    calls = fake_get(route_real)
    result = SearchChargersTool().invoke(GAPYEONG)

    assert result.ok, result.error
    ev_params = calls[1]["params"]
    assert (ev_params["zcode"], ev_params["zscode"]) == ("41", "41820")
    assert result.meta.actual["zscode"] == "41820"


def test_fast_chargers_grouped_by_station(fake_get):
    fake_get(route_real)
    result = SearchChargersTool().invoke({**GAPYEONG, "limit": 20})

    stations = result.data["stations"]
    assert result.data["station_count"] == 6                       # 급속 12기 → 충전소 6곳
    assert sum(len(s["chargers"]) for s in stations) == 12
    assert all(c["output_kw"] >= 50 for s in stations for c in s["chargers"])
    assert all(s["distance_km"] <= 10 for s in stations)


def test_available_stations_come_first(fake_get):
    fake_get(route_real)
    stations = SearchChargersTool().invoke({**GAPYEONG, "limit": 20}).data["stations"]

    flags = [s["available_count"] == 0 for s in stations]
    assert flags == sorted(flags)                                   # 사용 가능(False) → 불가(True)
    first = stations[0]
    assert first["available_count"] >= 1
    assert {"status", "status_label", "status_updated_at", "type"} <= first["chargers"][0].keys()


def test_snapshot_and_boundary_are_explained(fake_get):
    fake_get(route_real)
    notes = SearchChargersTool().invoke(GAPYEONG).meta.notes
    assert any("시군구" in n for n in notes)
    assert any("조회 시점" in n for n in notes)


def test_slow_chargers_when_not_fast_only(fake_get):
    fake_get(route_real)
    result = SearchChargersTool().invoke({**GAPYEONG, "fast_only": False, "limit": 20})
    assert sum(len(s["chargers"]) for s in result.data["stations"]) > 12


def test_status_codes_are_mapped(fake_get):
    payload = load_fixture("ev_charger_gapyeong.json")
    for it, stat in zip(payload["items"]["item"], ["4", "5", "9", "1"]):
        it.update({"stat": stat, "output": "100", "lat": "37.8315", "lng": "127.5097", "statId": "X1"})
    fake_get(lambda url, p: FakeResponse(load_fixture("kakao_region_gapyeong.json")) if "kakao" in url else FakeResponse(payload))
    chargers = SearchChargersTool().invoke({**GAPYEONG, "limit": 20}).data["stations"]
    statuses = {c["status"] for s in chargers for c in s["chargers"]}
    assert {"OUT_OF_SERVICE", "MAINTENANCE", "UNKNOWN", "COMM_ERROR"} <= statuses


def test_no_station_in_radius(fake_get):
    fake_get(route_real)
    result = SearchChargersTool().invoke({**GAPYEONG, "radius_km": 1, "min_output_kw": 350})
    assert result.error.code is ErrorCode.NO_RESULT
