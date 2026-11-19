"""요청 분석 결과 · 대화 조건 · 그래프 상태."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

Intent = Literal["PLAN_TRIP", "FACT_QUERY", "MODIFY", "CONFIRM_PLAN", "REGISTER_CALENDAR", "CHITCHAT"]
FactTarget = Literal["campsites", "chargers", "weather"]


class DeviceUse(BaseModel):
    name: str = Field(description="기기 이름 (예: 전기장판)")
    hours: float | None = Field(default=None, description="사용 시간(시간). 말하지 않았으면 null")
    power_w: int | None = Field(default=None, description="소비전력(W). 말하지 않았으면 null")


class Analysis(BaseModel):
    """LLM이 사용자 메시지에서 뽑는 값. 말하지 않은 값은 null로 둔다 (추측 금지)."""

    intent: Intent = Field(description="PLAN_TRIP 차박 계획 / FACT_QUERY 사실 조회 / MODIFY 직전 조건 일부 변경 / "
                                       "CONFIRM_PLAN 직전 추천 저장 · 확정 / REGISTER_CALENDAR 저장한 계획을 캘린더에 등록 / "
                                       "CHITCHAT 인사 · 사용법 · 그 밖의 대화")
    intent_reason: str = Field(description="그 의도로 본 이유 한 문장")
    start_date: date | None = Field(default=None, description="출발 날짜 (상대 표현은 오늘 기준으로 계산)")
    nights: int | None = Field(default=None, ge=1, le=7, description="박 수")
    origin: str | None = Field(default=None, description="출발지 주소 · 지명")
    region: str | None = Field(default=None, description="캠핑할 지역 · 캠핑장 이름")
    arrival_time: str | None = Field(default=None, description="캠핑장 도착 희망 시각 HH:MM")
    capacity_kwh: float | None = Field(default=None, description="배터리 용량 kWh (말한 경우만)")
    soc_start_pct: float | None = Field(default=None, description="현재 배터리 잔량 % (0~100)")
    efficiency_km_per_kwh: float | None = Field(default=None, description="전비 km/kWh (말한 경우만)")
    min_return_pct: float | None = Field(default=None, description="귀가 때 남길 최소 잔량 %")
    devices: list[DeviceUse] | None = Field(default=None, description="캠핑장에서 쓸 기기 목록. 말하지 않았으면 null")
    removed_devices: list[str] | None = Field(default=None, description="이제 안 쓰겠다고 한 기기 이름. 없으면 null")
    prefer_electric: bool | None = Field(default=None, description="전기 사이트 캠핑장을 원하는지")
    allow_charging: bool | None = Field(default=None, description="가는 길 충전을 허용하는지 (거부하면 false)")
    fast_only: bool | None = Field(default=None, description="급속 충전기만 원하는지")
    fact_target: FactTarget | None = Field(default=None, description="FACT_QUERY일 때 무엇을 묻는지")
    plan_id: int | None = Field(default=None, description="REGISTER_CALENDAR에서 사용자가 계획 번호를 말한 경우만")


class Conditions(BaseModel):
    """대화 동안 유지하는 조건 슬롯. 새 분석 결과에서 값이 있는 항목만 덮어쓴다."""

    start_date: date | None = None
    nights: int | None = None
    origin: str | None = None
    region: str | None = None
    arrival_time: str | None = None
    capacity_kwh: float | None = None
    soc_start_pct: float | None = None
    efficiency_km_per_kwh: float | None = None
    min_return_pct: float | None = None
    devices: list[DeviceUse] | None = None
    prefer_electric: bool | None = None
    allow_charging: bool | None = None
    fast_only: bool | None = None

    def merge(self, analysis: Analysis) -> tuple["Conditions", list[str]]:
        """값이 있는 항목만 갱신한 새 조건과, 바뀐 항목 이름을 돌려준다.

        기기는 이름 기준으로 합친다. 여러 턴에 나눠 말해도("전기난로 4시간" → "전기난로는 1000W")
        앞에서 말한 값이 남도록, 새로 말한 값만 갱신하고 말하지 않은 기기 · 값은 유지한다."""
        updates = {k: v for k, v in analysis.model_dump().items()
                   if k in Conditions.model_fields and v is not None and k != "devices"}
        devices = self._merge_devices(analysis.devices, analysis.removed_devices)
        if devices != self.devices:
            updates["devices"] = devices
        changed = [k for k, v in updates.items() if getattr(self, k) != v]
        return self.model_copy(update=updates), changed

    def _merge_devices(self, new: list[DeviceUse] | None, removed: list[str] | None) -> list[DeviceUse] | None:
        if new is None and not removed:
            return self.devices
        merged = {d.name: d.model_copy() for d in (self.devices or [])}
        for d in new or []:
            old = merged.get(d.name)
            merged[d.name] = d.model_copy() if old is None else old.model_copy(update={
                k: v for k, v in d.model_dump().items() if v is not None})
        for name in removed or []:
            merged.pop(name, None)
        return list(merged.values()) or None

    @property
    def end_date(self) -> date | None:
        if self.start_date is None:
            return None
        return self.start_date + timedelta(days=self.nights or 1)

    def missing_for_plan(self) -> list[str]:
        """차박 계획 계산에 꼭 필요한데 비어 있는 항목 (사용자에게 되물을 것)."""
        missing = []
        for key, label in [("start_date", "출발 날짜"), ("origin", "출발지"), ("region", "캠핑할 지역"),
                           ("capacity_kwh", "배터리 용량(kWh)"), ("soc_start_pct", "현재 배터리 잔량(%)")]:
            if getattr(self, key) in (None, ""):
                missing.append(label)
        if not self.devices:
            missing.append("캠핑장에서 쓸 기기")
        elif any(d.hours is None for d in self.devices):
            missing.append("기기별 사용 시간: " + ", ".join(d.name for d in self.devices if d.hours is None))
        return missing


class AgentState(TypedDict, total=False):
    session_id: str
    turn_id: str
    message: str
    intent: Intent
    intent_reason: str
    fact_target: FactTarget | None
    conditions: Conditions
    changed: list[str]
    missing: list[str]
    places: dict[str, Any]                  # 지오코딩 결과 {"origin": {...}, "region": {...}}
    plan: list[dict[str, Any]]               # [{step_id, tool, args, purpose, fixed}]
    executed: list[str]                      # 실행 끝난 step_id
    results: dict[str, Any]                  # step_id → ToolResult(dict)
    evaluations: list[dict[str, Any]]
    replan_count: int
    strategies_used: list[str]
    replans: list[dict[str, Any]]
    stop_reason: str | None
    status: str
    answer: str
    output: dict[str, Any]
    cache_ref: dict[str, Any]                # 한 턴 안의 Tool 결과 캐시 (재계획 때 재사용)
    register_plan_id: int                    # REGISTER_CALENDAR: 등록할 계획 번호 (저장소에서 찾은 값)
