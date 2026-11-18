"""길찾기 Tool: 실제 도로 주행 거리 · 시간."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.clients import kakao_mobility
from app.tools.base import Tool, ToolMeta


class Point(BaseModel):
    lat: float = Field(ge=33, le=39)
    lon: float = Field(ge=124, le=132)


class RouteInput(BaseModel):
    origin: Point = Field(description="출발 좌표")
    destination: Point = Field(description="도착 좌표 (캠핑장)")
    waypoint: Point | None = Field(default=None, description="들를 곳 좌표 (충전소). 없으면 생략")
    ref_id: str | None = Field(default=None, description="어떤 후보에 대한 경로인지 추적용 id (예: campsite_id)")


class GetRouteTool(Tool):
    name = "get_route"
    description = (
        "출발지에서 캠핑장까지(또는 충전소를 거쳐 가는) 실제 도로 주행 거리와 시간이 "
        "배터리 판단에 필요할 때 쓴다. 편도 기준으로 돌려준다."
    )
    input_model = RouteInput
    source = kakao_mobility.SOURCE

    def run(self, args: RouteInput, meta: ToolMeta) -> dict[str, Any]:
        waypoints = [(args.waypoint.lat, args.waypoint.lon)] if args.waypoint else None
        route = kakao_mobility.directions((args.origin.lat, args.origin.lon),
                                          (args.destination.lat, args.destination.lon), waypoints)
        summary = route.get("summary", {})
        legs = [{"distance_km": round(s.get("distance", 0) / 1000, 1),
                 "duration_min": round(s.get("duration", 0) / 60)} for s in route.get("sections", [])]
        meta.notes.append("돌아오는 길은 가는 길과 같은 거리로 가정합니다")
        return {
            "ref_id": args.ref_id,
            "distance_km": round(summary.get("distance", 0) / 1000, 1),
            "duration_min": round(summary.get("duration", 0) / 60),
            "legs": legs,
            "via_waypoint": bool(waypoints),
        }
