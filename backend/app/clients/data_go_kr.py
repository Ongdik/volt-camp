"""공공데이터포털(apis.data.go.kr) 공통 클라이언트.

오류가 HTTP 200으로 오는 경우가 있어 본문으로 판정한다.
  - 게이트웨이 오류: XML `OpenAPI_ServiceResponse.cmmMsgHeader.returnReasonCode`
  - 기관 오류: `resultCode` (정상 00 / 0000)
  - 0건: `items`가 빈 문자열
"""

from __future__ import annotations

from typing import Any

from app.clients import http
from app.config import get_settings
from app.tools.base import ErrorCode, ToolFailure

GATEWAY_CODES = {
    "20": ErrorCode.AUTH_FAILED,      # 서비스 접근 거부 (활용신청 안 됨)
    "22": ErrorCode.QUOTA_EXCEEDED,   # 일일 호출 한도 초과
    "30": ErrorCode.AUTH_FAILED,      # 등록되지 않은 키
    "31": ErrorCode.AUTH_FAILED,      # 기한 만료 키
}
OK_RESULT_CODES = {"00", "0000"}
NO_DATA_RESULT_CODES = {"03"}


def _find(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find(v, key)
            if found is not None:
                return found
    return None


def request(url: str, params: dict[str, Any], *, source: str) -> Any:
    """서비스키를 붙여 호출하고, 오류면 ToolFailure. 정상이면 본문 dict."""
    key = get_settings().data_go_kr_key
    if not key:
        raise ToolFailure(ErrorCode.MISSING_API_KEY, "공공데이터포털 API 키가 설정되지 않았습니다")

    resp = http.get(url, params={"serviceKey": key, **params}, source=source)
    body = http.parse_body(resp, source)

    reason = _find(body, "returnReasonCode")
    auth_msg = _find(body, "returnAuthMsg")
    if auth_msg and auth_msg != "NORMAL_SERVICE":
        code = GATEWAY_CODES.get(str(reason), ErrorCode.UPSTREAM_ERROR)
        if code is ErrorCode.AUTH_FAILED:
            raise ToolFailure(code, f"{source} 이용 권한이 없습니다. 활용신청과 API 키를 확인해 주세요")
        raise ToolFailure(code, f"{source} 호출에 실패했습니다")

    result_code = _find(body, "resultCode")
    if result_code is not None and str(result_code) not in OK_RESULT_CODES:
        if str(result_code) in NO_DATA_RESULT_CODES:
            raise ToolFailure(ErrorCode.NO_RESULT, f"{source}에서 조건에 맞는 자료가 없습니다")
        raise ToolFailure(ErrorCode.UPSTREAM_ERROR, f"{source} 오류: {_find(body, 'resultMsg') or result_code}")
    if not resp.ok:
        raise ToolFailure(ErrorCode.UPSTREAM_ERROR, f"{source} 호출에 실패했습니다 (HTTP {resp.status_code})")
    return body


def items(body: Any) -> list[dict[str, Any]]:
    """`items.item`을 목록으로. 0건(빈 문자열)이면 빈 목록, 1건(dict)이면 한 개짜리 목록."""
    container = _find(body, "items")
    if not container or not isinstance(container, dict):
        return []
    item = container.get("item")
    if isinstance(item, dict):
        return [item]
    if isinstance(item, list):
        return [x for x in item if isinstance(x, dict)]
    return []


def total_count(body: Any) -> int:
    try:
        return int(_find(body, "totalCount") or 0)
    except (TypeError, ValueError):
        return 0
