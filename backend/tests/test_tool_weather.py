from datetime import datetime

import pytest

from app.clients.kma import KST, latest_base, latlon_to_grid
from app.tools.base import ErrorCode
from app.tools.external.weather import GetWeatherTool, parse_precip_mm
from tests.conftest import FakeResponse, load_fixture

GAPYEONG = {"lat": 37.8315, "lon": 127.5097}   # 샘플: 2026-11-18 14시 발표, 11/18 ~ 11/22


@pytest.mark.parametrize("lat, lon, grid", [
    (37.5665, 126.9780, (60, 127)),   # 서울시청
    (33.4996, 126.5312, (53, 38)),    # 제주
    (37.8315, 127.5097, (69, 133)),   # 가평 (#1 실행 결과와 같은 격자)
])
def test_grid_conversion(lat, lon, grid):
    assert latlon_to_grid(lat, lon) == grid


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 11, 18, 15, 7, tzinfo=KST), ("20261118", "1400")),
    (datetime(2026, 11, 18, 14, 5, tzinfo=KST), ("20261118", "1100")),   # 14시 발표 직후 10분은 아직 없음
    (datetime(2026, 11, 18, 1, 0, tzinfo=KST), ("20261117", "2300")),    # 새벽은 전날 23시
])
def test_latest_base(now, expected):
    assert latest_base(now) == expected


@pytest.mark.parametrize("text, mm", [
    ("강수없음", 0.0), ("1mm 미만", 0.5), ("2.0mm", 2.0), ("30.0~50.0mm", 30.0), ("50.0mm 이상", 50.0), (None, 0.0),
])
def test_parse_precip(text, mm):
    assert parse_precip_mm(text) == mm


def test_hourly_and_summary_from_real_forecast(fake_get):
    calls = fake_get(FakeResponse(load_fixture("kma_vilage_fcst.json")))
    result = GetWeatherTool().invoke({**GAPYEONG, "start_date": "2026-11-19", "end_date": "2026-11-20"})

    assert result.ok, result.error
    hourly = result.data["hourly"]
    assert hourly[0]["time"] == "2026-11-19T00:00" and hourly[-1]["time"] == "2026-11-20T23:00"
    assert len(hourly) == 48
    s = result.data["summary"]
    assert s["min_temp_c"] <= s["night_min_temp_c"] <= s["max_temp_c"]
    assert 0 <= s["max_precip_prob"] <= 100
    assert result.data["forecast_range"] == ["2026-11-18", "2026-11-22"]
    assert calls[0]["params"]["nx"] == 69 and calls[0]["params"]["ny"] == 133
    assert result.meta.notes == []


def test_partly_out_of_range_is_explained(fake_get):
    fake_get(FakeResponse(load_fixture("kma_vilage_fcst.json")))
    result = GetWeatherTool().invoke({**GAPYEONG, "start_date": "2026-11-22", "end_date": "2026-11-24"})

    assert result.ok
    assert result.meta.actual["to"] == "2026-11-22"
    assert any("2026-11-22까지만" in n for n in result.meta.notes)


def test_fully_out_of_range(fake_get):
    fake_get(FakeResponse(load_fixture("kma_vilage_fcst.json")))
    result = GetWeatherTool().invoke({**GAPYEONG, "start_date": "2026-12-12", "end_date": "2026-12-13"})

    assert result.error.code is ErrorCode.OUT_OF_RANGE
    assert "2026-11-22" in result.error.message


def test_dates_must_be_ordered():
    result = GetWeatherTool().invoke({**GAPYEONG, "start_date": "2026-11-20", "end_date": "2026-11-19"})
    assert result.error.code is ErrorCode.INVALID_INPUT
