"""기상청 단기예보 클라이언트: 격자 변환, 최근 발표 시각, 단기예보 조회."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from app.clients import data_go_kr

URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
SOURCE = "기상청 단기예보"
KST = timezone(timedelta(hours=9))
BASE_HOURS = (2, 5, 8, 11, 14, 17, 20, 23)   # 발표 시각. 발표 후 약 10분 뒤부터 조회 가능


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도 → 동네예보 격자(nx, ny). 기상청 Lambert Conformal Conic 변환."""
    re = 6371.00877 / 5.0
    slat1, slat2, olon, olat = (math.radians(v) for v in (30.0, 60.0, 126.0, 38.0))
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(
        math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5))
    sf = math.pow(math.tan(math.pi * 0.25 + slat1 * 0.5), sn) * math.cos(slat1) / sn
    ro = re * sf / math.pow(math.tan(math.pi * 0.25 + olat * 0.5), sn)
    ra = re * sf / math.pow(math.tan(math.pi * 0.25 + math.radians(lat) * 0.5), sn)
    theta = math.radians(lon) - olon
    theta = (theta + math.pi) % (2 * math.pi) - math.pi
    theta *= sn
    return int(math.floor(ra * math.sin(theta) + 43 + 0.5)), int(math.floor(ro - ra * math.cos(theta) + 136 + 0.5))


def latest_base(now: datetime | None = None) -> tuple[str, str]:
    """지금 조회할 수 있는 가장 최근 발표 (base_date, base_time)."""
    now = (now or datetime.now(KST)).astimezone(KST) - timedelta(minutes=10)
    hours = [h for h in BASE_HOURS if h <= now.hour]
    if not hours:
        now -= timedelta(days=1)
        hours = [23]
    return now.strftime("%Y%m%d"), f"{hours[-1]:02d}00"


def vilage_forecast(lat: float, lon: float, now: datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(예보 항목 목록, 조회 기준)을 돌려준다."""
    nx, ny = latlon_to_grid(lat, lon)
    base_date, base_time = latest_base(now)
    body = data_go_kr.request(URL, {"pageNo": 1, "numOfRows": 1500, "dataType": "JSON",
                                    "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny},
                              source=SOURCE)
    return data_go_kr.items(body), {"base": f"{base_date} {base_time}", "grid": [nx, ny]}
