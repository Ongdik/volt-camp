"""캠핑장 후보 조회 Tool (고캠핑 위치 기반 목록)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.clients import data_go_kr, kakao
from app.geo import haversine_km
from app.tools.base import ErrorCode, Tool, ToolFailure, ToolMeta

URL = "https://apis.data.go.kr/B551011/GoCamping/locationBasedList"
SOURCE = "한국관광공사 고캠핑"
FETCH_ROWS = 100       # 전기 필터 · 거리 정렬을 위해 넉넉히 받아 온다
MAX_RADIUS_KM = 20


class CampsiteSearchInput(BaseModel):
    region: str | None = Field(default=None, description="찾을 지역명 (예: 가평, 홍천). region 또는 lat/lon 중 하나는 필요")
    lat: float | None = Field(default=None, ge=33, le=39, description="중심 위도")
    lon: float | None = Field(default=None, ge=124, le=132, description="중심 경도")
    radius_km: int = Field(default=10, ge=1, le=MAX_RADIUS_KM, description="탐색 반경(km), 최대 20")
    require_electric: bool = Field(default=False, description="전기 사용 가능한 캠핑장만")
    limit: int = Field(default=10, ge=1, le=30, description="최대 후보 수")

    @model_validator(mode="after")
    def need_center(self):
        if not self.region and (self.lat is None or self.lon is None):
            raise ValueError("region 또는 lat/lon이 필요합니다")
        return self


def electric_of(facilities: str) -> bool | None:
    """부대시설 문자열로 전기 사용 여부 판정. 미기재면 확인 불가(None)."""
    if not facilities or not facilities.strip():
        return None
    return "전기" in facilities


def _split(text: str) -> list[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def to_candidate(item: dict[str, Any], lat: float, lon: float) -> dict[str, Any] | None:
    try:
        c_lat, c_lon = float(item["mapY"]), float(item["mapX"])
    except (KeyError, TypeError, ValueError):
        return None  # 좌표가 없으면 다음 Tool 입력을 채울 수 없으므로 후보에서 뺀다
    return {
        "campsite_id": str(item.get("contentId", "")),
        "name": item.get("facltNm", ""),
        "intro": item.get("lineIntro", ""),
        "address": item.get("addr1", ""),
        "lat": c_lat,
        "lon": c_lon,
        "distance_km": round(haversine_km(lat, lon, c_lat, c_lon), 1),
        "types": _split(item.get("induty", "")),
        "facilities": _split(item.get("sbrsCl", "")),
        "has_electric": electric_of(item.get("sbrsCl", "")),
        "tel": item.get("tel", ""),
        "homepage": item.get("homepage", ""),
        "reservation_url": item.get("resveUrl", ""),
    }


class SearchCampsitesTool(Tool):
    name = "search_campsites"
    description = (
        "사용자가 아직 캠핑장을 정하지 않았거나, 조건을 못 맞춰 다른 캠핑장 대안이 필요할 때 쓴다. "
        "지역명 또는 좌표 주변의 캠핑장 후보와 좌표 · 전기 사용 가능 여부를 가까운 순으로 돌려준다."
    )
    input_model = CampsiteSearchInput
    source = SOURCE

    def run(self, args: CampsiteSearchInput, meta: ToolMeta) -> dict[str, Any]:
        lat, lon, center_label = args.lat, args.lon, args.region
        if lat is None or lon is None:
            place = kakao.geocode(args.region)
            lat, lon, center_label = place.lat, place.lon, place.address
            meta.notes.append(f"'{args.region}'을(를) '{place.address}' 기준으로 찾았습니다")

        body = data_go_kr.request(URL, {
            "MobileOS": "ETC", "MobileApp": "VoltCamp", "_type": "json",
            "pageNo": 1, "numOfRows": FETCH_ROWS,
            "mapX": lon, "mapY": lat, "radius": args.radius_km * 1000,
        }, source=SOURCE)
        meta.actual = {"center": {"lat": lat, "lon": lon, "label": center_label}, "radius_km": args.radius_km}

        candidates = [c for c in (to_candidate(it, lat, lon) for it in data_go_kr.items(body)) if c]
        if args.require_electric:
            unknown = sum(1 for c in candidates if c["has_electric"] is None)
            candidates = [c for c in candidates if c["has_electric"] is True]
            if unknown:
                meta.notes.append(f"부대시설 정보가 없어 전기 사용 여부를 확인할 수 없는 {unknown}곳은 제외했습니다")
        if not candidates:
            cond = "전기 사용 가능한 " if args.require_electric else ""
            raise ToolFailure(ErrorCode.NO_RESULT, f"반경 {args.radius_km}km 안에 {cond}캠핑장이 없습니다")

        candidates.sort(key=lambda c: c["distance_km"])
        return {
            "candidates": candidates[: args.limit],
            "total_in_radius": data_go_kr.total_count(body),
        }
