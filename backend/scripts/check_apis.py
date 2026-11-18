"""외부 API · LLM 연결 확인 스크립트.

각 API를 실제로 한 번씩 호출해서
  1) 키 발급 · 호출 가능 여부
  2) Tool 출력 구조를 채울 수 있는 필드가 응답에 있는지
  3) 결과가 비었을 때(0건 · 경로 없음 · 예보 범위 밖) 실제 응답 모양
을 확인하고, 응답 원문을 backend/api_samples/ 에 저장한다. (키 값은 저장하지 않는다)

사용법 (backend/ 에서):
    python scripts/check_apis.py                  # 전체
    python scripts/check_apis.py --only weather,route
    python scripts/check_apis.py --no-save        # 응답 원문 저장 안 함

필요 패키지: requests, python-dotenv, openai
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")

SAMPLE_DIR = BACKEND_DIR / "api_samples"
TIMEOUT = float(os.getenv("TOOL_TIMEOUT_SEC", "10"))
SAVE_SAMPLES = True

# 확인용 기준점: 출발지(주소)와 캠핑 후보 지역(경기 가평 일대)
ORIGIN_ADDRESS = os.getenv("CHECK_ORIGIN_ADDRESS", "서울특별시 중구 세종대로 110")
CAMP_LAT, CAMP_LON = 37.8315, 127.5097
CAMP_ZCODE = "41"  # 충전소 API 시도 코드 (경기도)


# ---------------------------------------------------------------- 결과 모델

@dataclass
class CheckResult:
    name: str
    ok: bool = False
    code: str = ""
    elapsed_ms: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        self.notes.append(msg)


# ---------------------------------------------------------------- 공통 유틸

def http_get(url: str, *, params: dict | None = None, headers: dict | None = None):
    """GET 호출. 예외를 밖으로 던지지 않고 (response | None, elapsed_ms, error_code)로 돌려준다."""
    started = time.perf_counter()
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        return resp, int((time.perf_counter() - started) * 1000), ""
    except requests.Timeout:
        return None, int((time.perf_counter() - started) * 1000), "TIMEOUT"
    except requests.RequestException as exc:
        return None, int((time.perf_counter() - started) * 1000), f"UPSTREAM_ERROR({type(exc).__name__})"


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


def parse_body(resp: requests.Response) -> Any:
    """JSON이면 JSON으로, 아니면 XML을 dict로 바꾼다. 둘 다 아니면 앞부분 텍스트."""
    try:
        return resp.json()
    except ValueError:
        pass
    try:
        root = ET.fromstring(resp.content)
        return {root.tag: xml_to_dict(root)}
    except ET.ParseError:
        return {"_raw_text": resp.text[:500]}


def find_items(obj: Any) -> list[dict]:
    """응답 어디에 있든 첫 번째 '딕셔너리 목록'을 찾아 돌려준다. (item 하나면 dict로 오는 경우 포함)"""
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            return obj
        for x in obj:
            found = find_items(x)
            if found:
                return found
    elif isinstance(obj, dict):
        if "item" in obj:
            item = obj["item"]
            if isinstance(item, dict):
                return [item]
            if isinstance(item, list):
                return [x for x in item if isinstance(x, dict)]
        for value in obj.values():
            found = find_items(value)
            if found:
                return found
    return []


def find_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = find_value(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for x in obj:
            found = find_value(x, key)
            if found is not None:
                return found
    return None


def data_go_kr_error(body: Any) -> str:
    """공공데이터포털 게이트웨이 · 기관 오류를 실패 코드로 바꾼다. 정상이면 빈 문자열."""
    auth_msg = find_value(body, "returnAuthMsg")
    reason = find_value(body, "returnReasonCode")
    if auth_msg and auth_msg != "NORMAL_SERVICE":
        mapping = {"20": "AUTH_FAILED", "30": "AUTH_FAILED", "31": "AUTH_FAILED", "22": "QUOTA_EXCEEDED"}
        return f"{mapping.get(str(reason), 'UPSTREAM_ERROR')}({auth_msg})"
    result_code = find_value(body, "resultCode")
    if result_code not in (None, "", "00", "0000"):
        return f"UPSTREAM_ERROR(resultCode={result_code}, {find_value(body, 'resultMsg')})"
    return ""


def save_sample(name: str, body: Any, params: dict | None = None) -> None:
    if not SAVE_SAMPLES:
        return
    SAMPLE_DIR.mkdir(exist_ok=True)
    safe_params = {k: v for k, v in (params or {}).items() if "key" not in k.lower()}
    payload = {"params": safe_params, "body": body}
    (SAMPLE_DIR / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def require_env(result: CheckResult, *names: str) -> dict[str, str] | None:
    values = {n: os.getenv(n, "").strip() for n in names}
    missing = [n for n, v in values.items() if not v]
    if missing:
        result.code = "MISSING_API_KEY"
        result.note(f".env에 {', '.join(missing)} 값이 없습니다")
        return None
    return values


# ---------------------------------------------------------------- 실행

CHECKS: dict = {}


def main() -> int:
    global SAVE_SAMPLES
    parser = argparse.ArgumentParser(description="외부 API · LLM 연결 확인")
    parser.add_argument("--only", help=f"쉼표로 구분: {','.join(CHECKS)}")
    parser.add_argument("--no-save", action="store_true", help="응답 원문을 저장하지 않음")
    args = parser.parse_args()
    SAVE_SAMPLES = not args.no_save

    names = list(CHECKS) if not args.only else [n.strip() for n in args.only.split(",")]
    unknown = [n for n in names if n not in CHECKS]
    if unknown:
        parser.error(f"알 수 없는 항목: {unknown}")
    if "route" in names and "local" not in names:
        names.insert(names.index("route"), "local")  # 길찾기 출발 좌표용

    results = [CHECKS[n]() for n in names]

    print()
    for res in results:
        mark = "PASS" if res.ok else "FAIL"
        code = f" [{res.code}]" if res.code else ""
        print(f"[{mark}] {res.name}{code}  ({res.elapsed_ms} ms)")
        for line in res.notes:
            print(f"       {line}")
        print()

    passed = sum(r.ok for r in results)
    print(f"결과: {passed}/{len(results)} 통과", end="")
    print(f"  ·  응답 원문: {SAMPLE_DIR}" if SAVE_SAMPLES else "")

    if SAVE_SAMPLES:
        SAMPLE_DIR.mkdir(exist_ok=True)
        report = {
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "results": [r.__dict__ for r in results],
        }
        (SAMPLE_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
