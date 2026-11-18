"""카카오 로컬 API: 지오코딩(주소 · 키워드 → 좌표)과 좌표 → 법정동 코드."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.clients import http
from app.config import get_settings
from app.tools.base import ErrorCode, ToolFailure

BASE = "https://dapi.kakao.com/v2/local"
SOURCE = "카카오 로컬"


@dataclass(frozen=True)
class Place:
    name: str
    address: str
    lat: float
    lon: float


def _headers() -> dict[str, str]:
    key = get_settings().kakao_rest_key
    if not key:
        raise ToolFailure(ErrorCode.MISSING_API_KEY, "카카오 API 키가 설정되지 않았습니다")
    return {"Authorization": f"KakaoAK {key}"}


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = http.get(f"{BASE}/{path}", params=params, headers=_headers(), source=SOURCE)
    if not resp.ok:
        raise ToolFailure(ErrorCode.UPSTREAM_ERROR, f"{SOURCE} 호출에 실패했습니다 (HTTP {resp.status_code})")
    return http.parse_body(resp, SOURCE)


def geocode(query: str) -> Place:
    """주소 검색 → 없으면 키워드(장소명) 검색. 둘 다 없으면 NO_RESULT."""
    docs = _get("search/address.json", {"query": query}).get("documents", [])
    if docs:
        d = docs[0]
        return Place(name=query, address=d.get("address_name", query), lat=float(d["y"]), lon=float(d["x"]))
    docs = _get("search/keyword.json", {"query": query}).get("documents", [])
    if docs:
        d = docs[0]
        address = d.get("road_address_name") or d.get("address_name") or query
        return Place(name=d.get("place_name", query), address=address, lat=float(d["y"]), lon=float(d["x"]))
    raise ToolFailure(ErrorCode.NO_RESULT, f"'{query}' 위치를 찾지 못했습니다")


def region_code(lat: float, lon: float) -> str:
    """좌표의 법정동 코드(10자리). 앞 2자리는 시도, 앞 5자리는 시군구."""
    docs = _get("geo/coord2regioncode.json", {"x": lon, "y": lat}).get("documents", [])
    legal = next((d for d in docs if d.get("region_type") == "B"), docs[0] if docs else None)
    if not legal or not legal.get("code"):
        raise ToolFailure(ErrorCode.NO_RESULT, "좌표의 행정구역을 찾지 못했습니다")
    return str(legal["code"])
