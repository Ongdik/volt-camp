"""날씨 예보 Tool (기상청 단기예보): 기온 · 강수."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.clients import kma
from app.tools.base import ErrorCode, Tool, ToolFailure, ToolMeta

PTY_LABEL = {"0": "없음", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}
NIGHT_HOURS = set(range(18, 24)) | set(range(0, 7))


def parse_precip_mm(text: str | None) -> float:
    """기상청 강수량 문자열 → mm. '강수없음'=0, '1mm 미만'=0.5, 범위는 하한, '이상'은 그 값."""
    if not text or text in ("강수없음", "적설없음", "0"):
        return 0.0
    if "미만" in text:
        return 0.5
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    return float(nums[0]) if nums else 0.0


class WeatherInput(BaseModel):
    lat: float = Field(ge=33, le=39, description="위도 (캠핑장)")
    lon: float = Field(ge=124, le=132, description="경도 (캠핑장)")
    start_date: date = Field(description="시작 날짜 (출발일)")
    end_date: date = Field(description="끝 날짜 (귀가일)")

    @model_validator(mode="after")
    def ordered(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date는 start_date보다 빠를 수 없습니다")
        return self


def build_hourly(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_time: dict[str, dict[str, str]] = {}
    for it in items:
        key = f"{it.get('fcstDate')}{it.get('fcstTime')}"
        by_time.setdefault(key, {})[it.get("category")] = str(it.get("fcstValue"))
    hourly = []
    for key in sorted(by_time):
        v = by_time[key]
        if "TMP" not in v:
            continue
        t = datetime.strptime(key, "%Y%m%d%H%M")
        pty = v.get("PTY", "0")
        hourly.append({
            "time": t.strftime("%Y-%m-%dT%H:%M"),
            "temp_c": float(v["TMP"]),
            "precip_prob": int(v.get("POP", 0)),
            "precip_type": PTY_LABEL.get(pty, pty),
            "precip_mm": parse_precip_mm(v.get("PCP")),
        })
    return hourly


def summarize(hourly: list[dict[str, Any]]) -> dict[str, Any]:
    temps = [h["temp_c"] for h in hourly]
    night = [h["temp_c"] for h in hourly if int(h["time"][11:13]) in NIGHT_HOURS]
    return {
        "min_temp_c": min(temps),
        "max_temp_c": max(temps),
        "night_min_temp_c": min(night) if night else None,
        "max_precip_prob": max(h["precip_prob"] for h in hourly),
        "rain_hours": [h["time"] for h in hourly if h["precip_type"] != "없음" or h["precip_mm"] > 0],
    }


class GetWeatherTool(Tool):
    name = "get_weather"
    description = (
        "차박 날짜에 캠핑장의 기온(추위에 따른 배터리 효율 · 난방)과 강수(비 · 눈이 오면 실외 V2L 사용 불가)를 "
        "판단해야 할 때 쓴다. 기상청 단기예보라 오늘부터 약 4~5일 뒤까지만 알 수 있다."
    )
    input_model = WeatherInput
    source = kma.SOURCE

    def run(self, args: WeatherInput, meta: ToolMeta) -> dict[str, Any]:
        items, basis = kma.vilage_forecast(args.lat, args.lon)
        all_hours = build_hourly(items)
        if not all_hours:
            raise ToolFailure(ErrorCode.NO_RESULT, "예보 자료가 비어 있습니다")
        available_from, available_to = all_hours[0]["time"][:10], all_hours[-1]["time"][:10]
        start, end = args.start_date.isoformat(), args.end_date.isoformat()

        hourly = [h for h in all_hours if start <= h["time"][:10] <= end]
        if not hourly:
            raise ToolFailure(ErrorCode.OUT_OF_RANGE,
                              f"{start}~{end}은 예보 제공 범위({available_from}~{available_to}) 밖이라 날씨를 확인할 수 없습니다")

        covered_from, covered_to = hourly[0]["time"][:10], hourly[-1]["time"][:10]
        meta.actual = {"from": covered_from, "to": covered_to, **basis}
        if (covered_from, covered_to) != (start, end):
            meta.notes.append(f"예보는 {available_to}까지만 있어 {covered_from}~{covered_to} 구간만 확인했습니다")
        return {"hourly": hourly, "summary": summarize(hourly), "forecast_range": [available_from, available_to]}
