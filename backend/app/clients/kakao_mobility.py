"""카카오모빌리티 자동차 길찾기 클라이언트."""

from __future__ import annotations

from typing import Any

from app.clients import http
from app.clients.kakao import _headers
from app.tools.base import ErrorCode, ToolFailure

URL = "https://apis-navi.kakaomobility.com/v1/directions"
SOURCE = "카카오모빌리티 길찾기"


def _xy(lat: float, lon: float) -> str:
    return f"{lon},{lat}"   # 카카오는 '경도,위도' 순서


def directions(origin: tuple[float, float], destination: tuple[float, float],
               waypoints: list[tuple[float, float]] | None = None) -> dict[str, Any]:
    """첫 번째 경로(routes[0])를 돌려준다. 경로가 없으면(result_code != 0) NO_ROUTE."""
    params = {"origin": _xy(*origin), "destination": _xy(*destination), "priority": "RECOMMEND"}
    if waypoints:
        params["waypoints"] = "|".join(_xy(*w) for w in waypoints)
    resp = http.get(URL, params=params, headers=_headers(), source=SOURCE)
    if not resp.ok:
        raise ToolFailure(ErrorCode.UPSTREAM_ERROR, f"{SOURCE} 호출에 실패했습니다 (HTTP {resp.status_code})")
    routes = http.parse_body(resp, SOURCE).get("routes") or []
    route = routes[0] if routes else {}
    if route.get("result_code") != 0:
        raise ToolFailure(ErrorCode.NO_ROUTE, f"경로를 찾지 못했습니다: {route.get('result_msg', '결과 없음')}")
    return route
