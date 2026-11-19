"""메인 Agent 테스트용 가짜 LLM · Tool · 지오코더. 시나리오별로 결과를 고정해 흐름만 검증한다."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from app.agent.state import Analysis
from app.clients.kakao import Place
from app.tools.base import ErrorCode, Tool, ToolFailure, ToolMeta
from app.tools.registry import ToolRegistry

TODAY = date(2026, 11, 18)
ORIGIN = Place(name="서울시청", address="서울 중구 세종대로 110", lat=37.5663, lon=126.9779)
GAPYEONG = Place(name="가평", address="경기 가평군 가평읍", lat=37.8315, lon=127.5097)


def camp(cid: str, name: str, electric: bool | None, lat: float = 37.8, lon: float = 127.5) -> dict[str, Any]:
    return {"campsite_id": cid, "name": name, "intro": "", "address": f"가평 {name}", "lat": lat, "lon": lon,
            "distance_km": 3.0, "types": ["일반야영장"], "facilities": ["전기"] if electric else [],
            "has_electric": electric, "tel": "", "homepage": "", "reservation_url": ""}


MILD = {"min_temp_c": 2.0, "max_temp_c": 10.0, "night_min_temp_c": 5.0, "max_precip_prob": 20, "rain_hours": []}
RAINY = {**MILD, "max_precip_prob": 80, "rain_hours": ["2026-11-19T21:00"]}


class AnyInput(BaseModel):
    model_config = ConfigDict(extra="allow")


def stub(name: str, fn: Callable[[dict[str, Any], ToolMeta], dict[str, Any]], calls: list) -> Tool:
    def run(self, args, meta):
        raw = args.model_dump()
        calls.append((name, raw))
        return fn(raw, meta)
    cls = type(f"Stub_{name}", (Tool,), {"name": name, "description": f"stub {name}",
                                         "input_model": AnyInput, "run": run})
    return cls()


class World:
    """시나리오 설정: 캠핑장 목록, 거리, 날씨, 충전소, 실패시킬 Tool."""

    def __init__(self, campsites=None, electric_sites=None, wide_sites=None, distance_km=74.7, weather=MILD,
                 stations=None, fail: dict[str, ErrorCode] | None = None):
        self.campsites = campsites if campsites is not None else [camp("c1", "펫밀리 캠프", True), camp("c2", "축령산", None)]
        self.electric_sites = electric_sites
        self.wide_sites = wide_sites
        self.distance_km = distance_km
        self.weather = weather
        self.stations = stations if stations is not None else [
            {"station_id": "ME1", "name": "가평휴게소 급속", "address": "가평", "lat": 37.7, "lon": 127.4,
             "distance_km": 4.0, "available_count": 1, "max_output_kw": 100, "chargers": []}]
        self.fail = fail or {}
        self.calls: list = []

    def _maybe_fail(self, tool):
        if tool in self.fail:
            raise ToolFailure(self.fail[tool], f"{tool} 실패")

    def registry(self, extra: list[Tool] | None = None) -> ToolRegistry:
        def campsites(a, meta):
            self._maybe_fail("search_campsites")
            if a.get("require_electric") and self.electric_sites is not None:
                lst = self.electric_sites
            elif a.get("radius_km", 10) > 10 and self.wide_sites is not None:
                lst = self.wide_sites
            else:
                lst = [c for c in self.campsites if c["has_electric"] or not a.get("require_electric")]
            if not lst:
                raise ToolFailure(ErrorCode.NO_RESULT, "캠핑장이 없습니다")
            return {"candidates": lst, "total_in_radius": len(lst)}

        def route(a, meta):
            self._maybe_fail("get_route")
            meta.notes.append("돌아오는 길은 가는 길과 같은 거리로 가정합니다")
            if a.get("waypoint"):
                d = self.distance_km + 6
                legs = [{"distance_km": 45.0, "duration_min": 50}, {"distance_km": round(d - 45, 1), "duration_min": 30}]
                return {"ref_id": a.get("ref_id"), "distance_km": d, "duration_min": 110, "legs": legs, "via_waypoint": True}
            return {"ref_id": a.get("ref_id"), "distance_km": self.distance_km, "duration_min": 100,
                    "legs": [{"distance_km": self.distance_km, "duration_min": 100}], "via_waypoint": False}

        def weather(a, meta):
            self._maybe_fail("get_weather")
            if self.weather is None:
                raise ToolFailure(ErrorCode.OUT_OF_RANGE, "예보 범위 밖")
            return {"hourly": [], "summary": self.weather, "forecast_range": ["2026-11-18", "2026-11-22"]}

        def chargers(a, meta):
            self._maybe_fail("search_chargers")
            if not self.stations:
                raise ToolFailure(ErrorCode.NO_RESULT, "충전소 없음")
            return {"stations": self.stations, "station_count": len(self.stations)}

        return ToolRegistry([stub("search_campsites", campsites, self.calls), stub("get_route", route, self.calls),
                             stub("get_weather", weather, self.calls), stub("search_chargers", chargers, self.calls),
                             *(extra or [])])

    def geocode(self, text: str) -> Place:
        if "없는" in text:
            raise ToolFailure(ErrorCode.NO_RESULT, "위치 없음")
        return ORIGIN if "서울" in text or "시청" in text else GAPYEONG


class FakeLLM:
    """메시지 → Analysis를 미리 정해 둔 가짜 LLM. write()는 빈 문자열을 돌려 코드의 기본 문장을 쓰게 한다."""

    def __init__(self, script: dict[str, dict[str, Any]]):
        self.script = script
        self.analyze_calls = 0
        self.write_calls: list[tuple[str, dict]] = []

    def analyze(self, message: str, memory_summary: str, today: date) -> Analysis:
        self.analyze_calls += 1
        return Analysis(**self.script[message])

    def write(self, kind: str, context: dict[str, Any]) -> str:
        self.write_calls.append((kind, context))
        return ""


FULL_TRIP = {"intent": "PLAN_TRIP", "intent_reason": "날짜 · 지역 · 차량이 있음", "start_date": "2026-11-19",
             "nights": 1, "origin": "서울시청", "region": "가평", "capacity_kwh": 77.4, "soc_start_pct": 80,
             "min_return_pct": 30, "devices": [{"name": "전기장판", "hours": 8}]}
