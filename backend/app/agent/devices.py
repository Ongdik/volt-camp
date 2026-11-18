"""차량 · 기기 모델과 기기 표준값.

표준값은 흔한 캠핑용 제품의 정격 소비전력을 어림한 값이다. 사용자가 W를 말하면 그 값을 쓰고,
표준값을 쓴 경우에는 응답의 '가정'에 밝힌다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Location = Literal["indoor", "outdoor"]   # 차 안 콘센트 / 차 밖 V2L 커넥터


class DeviceSpec(BaseModel):
    power_w: int
    location: Location
    heating: bool = False    # 전열 기기: 차 안에서 쓰면 안 된다 (제조사 사용설명서)


# 이름(별칭 포함) → 표준값
DEVICE_CATALOG: dict[str, DeviceSpec] = {
    "전기장판": DeviceSpec(power_w=200, location="indoor"),
    "전기요": DeviceSpec(power_w=150, location="indoor"),
    "전기히터": DeviceSpec(power_w=1000, location="outdoor", heating=True),
    "히터": DeviceSpec(power_w=1000, location="outdoor", heating=True),
    "전기포트": DeviceSpec(power_w=1500, location="outdoor", heating=True),
    "전기주전자": DeviceSpec(power_w=1500, location="outdoor", heating=True),
    "인덕션": DeviceSpec(power_w=1500, location="outdoor", heating=True),
    "전기그릴": DeviceSpec(power_w=1300, location="outdoor", heating=True),
    "커피머신": DeviceSpec(power_w=1200, location="outdoor", heating=True),
    "캠핑냉장고": DeviceSpec(power_w=50, location="indoor"),
    "조명": DeviceSpec(power_w=20, location="outdoor"),
    "노트북": DeviceSpec(power_w=60, location="indoor"),
    "휴대폰충전": DeviceSpec(power_w=20, location="indoor"),
    "서큘레이터": DeviceSpec(power_w=30, location="indoor"),
    "빔프로젝터": DeviceSpec(power_w=150, location="outdoor"),
}


class Device(BaseModel):
    name: str
    power_w: int = Field(gt=0, le=5000)
    hours: float = Field(ge=0, le=48)
    location: Location = "indoor"
    heating: bool = False
    assumed_power: bool = False    # 정격을 표준값으로 채웠는지


def make_device(name: str, hours: float, power_w: int | None = None,
                location: Location | None = None) -> Device:
    """이름으로 표준값을 찾아 기기를 만든다. 사용자가 준 값이 있으면 그 값이 우선."""
    key = name.replace(" ", "")
    spec = DEVICE_CATALOG.get(key)
    if power_w is None and spec is None:
        raise ValueError(f"'{name}'의 소비전력을 알 수 없습니다")
    return Device(
        name=name,
        power_w=power_w or spec.power_w,
        hours=hours,
        location=location or (spec.location if spec else "indoor"),
        heating=spec.heating if spec else False,
        assumed_power=power_w is None,
    )


class Vehicle(BaseModel):
    capacity_kwh: float = Field(gt=10, le=200, description="배터리 용량(kWh)")
    soc_start: float = Field(gt=0, le=1, description="출발 잔량 (0~1)")
    efficiency_km_per_kwh: float = Field(default=5.0, gt=1, le=10, description="전비")
    v2l_limit: float = Field(default=0.20, ge=0.2, le=0.8, description="V2L 방전 제한 (차량 설정 20~80%)")
    v2l_max_w: int = Field(default=3500, ge=1000, le=10000, description="V2L 최대 출력(W)")
    min_return: float = Field(default=0.20, ge=0, le=0.9, description="귀가 시 남길 최소 잔량")
