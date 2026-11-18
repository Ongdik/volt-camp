"""배터리 제약 계산 (F3008). LLM은 수치를 만들지 않고, 이 모듈의 결과만 문장으로 옮긴다.

① 에너지  SOC_arrive = SOC0 − E_drive/C + E_charge/C
          SOC_after_camp = SOC_arrive − E_camp/C            (전기 사이트면 E_camp = 0)
          SOC_return = SOC_after_camp − E_drive/C
          충족: SOC_after_camp ≥ V2L 방전 제한  그리고  SOC_return ≥ 귀가 최소 잔량
② 출력    동시 사용 전력 합 ≤ V2L 최대 출력                 (전기 사이트면 검사하지 않음)
③ 강수    비 예보 + (실외 기기 또는 전열 기기) + 전기 사이트 아님 → 불가
          예보가 없으면 판단 불가로 둔다
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.devices import Device, Vehicle

# 기온별 주행 에너지 보정계수 (추울수록 전비가 떨어진다). 초기 가정값 — 출처 확인 후 조정
TEMP_FACTORS: list[tuple[float, float]] = [(10.0, 1.00), (0.0, 1.15), (-10.0, 1.30)]
COLDEST_FACTOR = 1.40
INVERTER_EFF = 0.90        # V2L 직류 → 교류 변환 손실
CHARGE_EFF = 0.85          # 급속충전 실효율 (충전 곡선 · 손실)
CHARGE_MARGIN = 1.10       # 충전 시간 여유 10%
RAIN_PROB_THRESHOLD = 60   # 강수확률(%) 이 값 이상이면 비 위험으로 본다

Constraint = Literal["ENERGY", "POWER", "RAIN"]


def temp_factor(min_temp_c: float | None) -> float:
    if min_temp_c is None:
        return 1.0
    for threshold, factor in TEMP_FACTORS:
        if min_temp_c >= threshold:
            return factor
    return COLDEST_FACTOR


class Evaluation(BaseModel):
    status: Literal["OK", "FAIL", "UNJUDGED"]
    failed: list[Constraint] = Field(default_factory=list)
    unjudged: list[Constraint] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    deficit_kwh: float = 0.0
    assumptions: list[str] = Field(default_factory=list)


def evaluate(vehicle: Vehicle, devices: list[Device], distance_km: float,
             weather: dict[str, Any] | None = None, has_electric: bool | None = None,
             charge_kwh: float = 0.0) -> Evaluation:
    """후보 한 곳에 대한 세 가지 제약 판정. distance_km는 편도."""
    C = vehicle.capacity_kwh
    site_power = has_electric is True
    min_temp = (weather or {}).get("night_min_temp_c", (weather or {}).get("min_temp_c"))
    factor = temp_factor(min_temp)
    assumptions = []
    if weather is None:
        assumptions.append("날씨 예보가 없어 기온 보정 없이(1.0) 계산했습니다")

    e_drive = distance_km / vehicle.efficiency_km_per_kwh * factor
    e_camp_raw = sum(d.power_w * d.hours for d in devices) / 1000 / INVERTER_EFF
    e_camp = 0.0 if site_power else e_camp_raw

    soc_arrive = vehicle.soc_start - e_drive / C + charge_kwh / C
    soc_after = soc_arrive - e_camp / C
    soc_return = soc_after - e_drive / C
    deficit = max((vehicle.v2l_limit - soc_after) * C, (vehicle.min_return - soc_return) * C, 0.0)

    ev = Evaluation(status="OK", assumptions=assumptions)
    ev.metrics = {
        "distance_km_oneway": round(distance_km, 1),
        "temp_factor": factor,
        "e_drive_oneway_kwh": round(e_drive, 2),
        "e_camp_kwh": round(e_camp, 2),
        "e_camp_from_site_kwh": round(e_camp_raw, 2) if site_power else 0.0,
        "charge_kwh": round(charge_kwh, 2),
        "soc": {"start": round(vehicle.soc_start, 3), "arrive": round(soc_arrive, 3),
                "after_camp": round(soc_after, 3), "return": round(soc_return, 3)},
        "limits": {"v2l_limit": vehicle.v2l_limit, "min_return": vehicle.min_return},
    }

    # ① 에너지
    if deficit > 0 or soc_arrive <= 0:
        ev.failed.append("ENERGY")
        ev.deficit_kwh = round(math.ceil(deficit * 10) / 10, 1)
        if soc_after < vehicle.v2l_limit:
            ev.reasons.append(f"캠핑 후 잔량 {soc_after:.0%}가 V2L 방전 제한 {vehicle.v2l_limit:.0%}보다 낮습니다")
        if soc_return < vehicle.min_return:
            ev.reasons.append(f"귀가 잔량 {soc_return:.0%}가 목표 {vehicle.min_return:.0%}보다 낮습니다")

    # ② 출력
    total_w = sum(d.power_w for d in devices)
    ev.metrics["total_power_w"] = total_w
    if not site_power and total_w > vehicle.v2l_max_w:
        ev.failed.append("POWER")
        ev.reasons.append(f"기기를 모두 켜면 {total_w:,}W로 V2L 최대 출력 {vehicle.v2l_max_w:,}W를 넘습니다")

    # ③ 강수
    if weather is None:
        ev.unjudged.append("RAIN")
        ev.reasons.append("예보 범위 밖이라 비 여부는 판단하지 못했습니다")
    else:
        rain = bool(weather.get("rain_hours")) or weather.get("max_precip_prob", 0) >= RAIN_PROB_THRESHOLD
        risky = [d.name for d in devices if d.location == "outdoor" or d.heating]
        ev.metrics["rain_expected"] = rain
        if rain and risky and not site_power:
            ev.failed.append("RAIN")
            ev.reasons.append(f"비 · 눈 예보가 있어 실외 V2L로 {', '.join(risky)}을(를) 쓸 수 없습니다 "
                              "(전열 기기는 차 안에서도 쓰면 안 됩니다)")

    if ev.failed:
        ev.status = "FAIL"
    elif ev.unjudged:
        ev.status = "OK"   # 판단한 제약은 모두 충족. 판단 못 한 항목은 unjudged로 따로 밝힌다
    return ev


def charge_minutes(deficit_kwh: float, output_kw: float) -> int:
    """부족량을 채우는 데 걸리는 충전 시간(분). 충전 곡선을 모르므로 실효율 + 여유로 어림한다."""
    if deficit_kwh <= 0:
        return 0
    return math.ceil(deficit_kwh / (output_kw * CHARGE_EFF) * 60 * CHARGE_MARGIN)


def latest_charger_arrival(campsite_arrival: datetime, charge_min: int, charger_to_camp_min: int) -> datetime:
    """캠핑장 도착 희망 시각에서 거꾸로: 늦어도 이 시각까지 충전소에 도착해야 한다."""
    return campsite_arrival - timedelta(minutes=charge_min + charger_to_camp_min)


def reduce_hours_suggestion(devices: list[Device], deficit_kwh: float) -> dict[str, Any] | None:
    """가장 많이 쓰는 기기 하나의 사용 시간을 줄여 부족량을 메울 수 있으면 그 제안."""
    if deficit_kwh <= 0 or not devices:
        return None
    biggest = max(devices, key=lambda d: d.power_w * d.hours)
    per_hour_kwh = biggest.power_w / 1000 / INVERTER_EFF
    cut = math.ceil(deficit_kwh / per_hour_kwh * 2) / 2   # 30분 단위
    if cut > biggest.hours:
        return None
    return {"device": biggest.name, "from_hours": biggest.hours, "to_hours": biggest.hours - cut}
