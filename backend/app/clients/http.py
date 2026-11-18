"""외부 HTTP 호출 공통 처리: 네트워크 · HTTP 상태 오류를 실패 코드로 바꾼다."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import requests

from app.config import get_settings
from app.tools.base import ErrorCode, ToolFailure


def get(url: str, *, params: dict | None = None, headers: dict | None = None, source: str = "") -> requests.Response:
    """GET 요청. 타임아웃 · 연결 실패 · 인증 · 한도 오류는 ToolFailure로 던진다."""
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=get_settings().tool_timeout_sec)
    except requests.Timeout:
        raise ToolFailure(ErrorCode.TIMEOUT, f"{source} 응답이 늦습니다. 잠시 후 다시 시도해 주세요") from None
    except requests.RequestException:
        raise ToolFailure(ErrorCode.UPSTREAM_ERROR, f"{source}에 연결하지 못했습니다") from None

    if resp.status_code in (401, 403):
        raise ToolFailure(ErrorCode.AUTH_FAILED, f"{source} 인증에 실패했습니다. API 키를 확인해 주세요")
    if resp.status_code == 429:
        raise ToolFailure(ErrorCode.QUOTA_EXCEEDED, f"{source} 호출 한도를 넘었습니다")
    if resp.status_code >= 500:
        raise ToolFailure(ErrorCode.UPSTREAM_ERROR, f"{source} 서버 오류입니다 (HTTP {resp.status_code})")
    return resp


def xml_to_dict(elem: ET.Element) -> Any:
    children = list(elem)
    if not children:
        return (elem.text or "").strip()
    out: dict[str, Any] = {}
    for child in children:
        value = xml_to_dict(child)
        if child.tag in out:
            if not isinstance(out[child.tag], list):
                out[child.tag] = [out[child.tag]]
            out[child.tag].append(value)
        else:
            out[child.tag] = value
    return out


def parse_body(resp: requests.Response, source: str = "") -> Any:
    """JSON을 우선, 아니면 XML을 dict로. 둘 다 아니면 PARSE_ERROR."""
    try:
        return resp.json()
    except ValueError:
        pass
    try:
        root = ET.fromstring(resp.content)
        return {root.tag: xml_to_dict(root)}
    except ET.ParseError:
        raise ToolFailure(ErrorCode.PARSE_ERROR, f"{source} 응답 형식을 읽지 못했습니다") from None
