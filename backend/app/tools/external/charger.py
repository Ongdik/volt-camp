"""충전소 검색 Tool (한국환경공단 전기자동차 충전소 정보, 실시간 상태 포함)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.clients import data_go_kr, kakao
from app.geo import haversine_km
from app.tools.base import ErrorCode, Tool, ToolFailure, ToolMeta

URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
SOURCE = "한국환경공단 전기자동차 충전소"

STATUS = {"1": "COMM_ERROR", "2": "AVAILABLE", "3": "CHARGING", "4": "OUT_OF_SERVICE", "5": "MAINTENANCE", "9": "UNKNOWN"}
STATUS_LABEL = {"COMM_ERROR": "통신이상", "AVAILABLE": "충전대기", "CHARGING": "충전중",
                "OUT_OF_SERVICE": "운영중지", "MAINTENANCE": "점검중", "UNKNOWN": "상태미확인"}
TYPE_LABEL = {"01": "DC차데모", "02": "AC완속", "03": "DC차데모+AC3상", "04": "DC콤보",
              "05": "DC차데모+DC콤보", "06": "DC차데모+AC3상+DC콤보", "07": "AC3상"}


class ChargerSearchInput(BaseModel):
    lat: float = Field(ge=33, le=39, description="찾을 중심 위도 (캠핑장 또는 경로 위 지점)")
    lon: float = Field(ge=124, le=132, description="찾을 중심 경도")
    radius_km: int = Field(default=10, ge=1, le=30, description="탐색 반경(km)")
    fast_only: bool = Field(default=True, description="급속 충전기만")
    min_output_kw: int = Field(default=50, ge=3, le=400, description="급속으로 볼 최소 출력(kW)")
    limit: int = Field(default=5, ge=1, le=20, description="최대 충전소 수")


def _ts(text: str) -> str | None:
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M")
    except (TypeError, ValueError):
        return None


def _kw(text: Any) -> int:
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def group_stations(items: list[dict[str, Any]], lat: float, lon: float, args: ChargerSearchInput) -> list[dict[str, Any]]:
    stations: dict[str, dict[str, Any]] = {}
    for it in items:
        if it.get("delYn") == "Y":
            continue
        output = _kw(it.get("output"))
        if args.fast_only and output < args.min_output_kw:
            continue
        try:
            s_lat, s_lon = float(it["lat"]), float(it["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        distance = haversine_km(lat, lon, s_lat, s_lon)
        if distance > args.radius_km:
            continue
        sid = it.get("statId", "")
        station = stations.setdefault(sid, {
            "station_id": sid, "name": it.get("statNm", ""), "address": it.get("addr", ""),
            "lat": s_lat, "lon": s_lon, "distance_km": round(distance, 1),
            "use_time": it.get("useTime", ""), "operator": it.get("busiNm", ""),
            "restricted": it.get("limitYn") == "Y", "chargers": [],
        })
        status = STATUS.get(str(it.get("stat")), "UNKNOWN")
        station["chargers"].append({
            "charger_id": it.get("chgerId", ""),
            "type": TYPE_LABEL.get(it.get("chgerType", ""), f"기타({it.get('chgerType', '')})"),
            "output_kw": output,
            "status": status,
            "status_label": STATUS_LABEL[status],
            "status_updated_at": _ts(it.get("statUpdDt", "")),
        })
    for s in stations.values():
        s["available_count"] = sum(c["status"] == "AVAILABLE" for c in s["chargers"])
        s["max_output_kw"] = max(c["output_kw"] for c in s["chargers"])
    return sorted(stations.values(), key=lambda s: (s["available_count"] == 0, s["distance_km"]))


class SearchChargersTool(Tool):
    name = "search_chargers"
    description = (
        "배터리가 모자라 충전소를 들러야 하거나, 사용자가 어떤 장소 주변 충전기를 물을 때 쓴다. "
        "주변 충전소와 충전기별 실시간 상태(충전대기 · 충전중 · 고장 등)를 돌려준다."
    )
    input_model = ChargerSearchInput
    source = SOURCE

    def run(self, args: ChargerSearchInput, meta: ToolMeta) -> dict[str, Any]:
        code = kakao.region_code(args.lat, args.lon)
        zcode, zscode = code[:2], code[:5]
        body = data_go_kr.request(URL, {"pageNo": 1, "numOfRows": 9999, "dataType": "JSON",
                                        "zcode": zcode, "zscode": zscode}, source=SOURCE)
        meta.actual = {"zcode": zcode, "zscode": zscode, "radius_km": args.radius_km}
        meta.notes.append("같은 시군구 안의 충전소만 찾았습니다")

        stations = group_stations(data_go_kr.items(body), args.lat, args.lon, args)
        if not stations:
            kind = f"{args.min_output_kw}kW 이상 급속 " if args.fast_only else ""
            raise ToolFailure(ErrorCode.NO_RESULT, f"반경 {args.radius_km}km 안에 {kind}충전소가 없습니다")
        meta.notes.append("충전기 상태는 조회 시점 기준이라 도착했을 때 비어 있다고 보장할 수 없습니다")
        return {"stations": stations[: args.limit], "station_count": len(stations)}
