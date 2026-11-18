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


# ---------------------------------------------------------------- 1. 고캠핑

def check_campsite() -> CheckResult:
    r = CheckResult("고캠핑 (캠핑장 후보)")
    env = require_env(r, "DATA_GO_KR_SERVICE_KEY")
    if not env:
        return r

    base = "https://apis.data.go.kr/B551011/GoCamping"
    common = {
        "serviceKey": env["DATA_GO_KR_SERVICE_KEY"],
        "MobileOS": "ETC",
        "MobileApp": "VoltCampCheck",
        "_type": "json",
        "pageNo": 1,
        "numOfRows": 10,
    }

    params = {**common, "mapX": CAMP_LON, "mapY": CAMP_LAT, "radius": 20000}
    resp, r.elapsed_ms, err = http_get(f"{base}/locationBasedList", params=params)
    op = "locationBasedList"
    if resp is not None and resp.status_code == 404:
        # 위치 기반 오퍼레이션이 없으면 전체 목록으로 확인
        params = dict(common)
        resp, r.elapsed_ms, err = http_get(f"{base}/basedList", params=params)
        op = "basedList"
    if err:
        r.code = err
        return r

    body = parse_body(resp)
    save_sample("campsite_ok", body, params)
    r.code = data_go_kr_error(body) or ("" if resp.ok else f"HTTP_{resp.status_code}")
    if r.code:
        return r

    items = find_items(body)
    r.ok = True
    r.note(f"오퍼레이션 {op}, 받은 건수 {len(items)}, totalCount={find_value(body, 'totalCount')}")
    if items:
        r.note(f"필드: {', '.join(sorted(items[0].keys()))}")
        electric = [it for it in items if "전기" in str(it.get("sbrsCl", ""))]
        r.note(f"부대시설(sbrsCl)에 '전기' 포함: {len(electric)}/{len(items)}건  ← has_electric 매핑 확인")
        for it in items[:3]:
            r.note(f"  - {it.get('facltNm')} | 업종 {it.get('induty')} | 부대시설 {it.get('sbrsCl')} | "
                   f"좌표 ({it.get('mapY')}, {it.get('mapX')})")

    # 결과 0건일 때의 응답 모양
    params = {**common, "keyword": "존재하지않는캠핑장테스트"}
    resp, _, err = http_get(f"{base}/searchList", params=params)
    if resp is not None:
        empty_body = parse_body(resp)
        save_sample("campsite_empty", empty_body, params)
        r.note(f"0건 응답: HTTP {resp.status_code}, 항목 {len(find_items(empty_body))}건, "
               f"items 값 형태 = {type(find_value(empty_body, 'items')).__name__}")
    return r


# ---------------------------------------------------------------- 2. 전기차 충전소

def check_charger() -> CheckResult:
    r = CheckResult("전기차 충전소 (위치 · 상태)")
    env = require_env(r, "DATA_GO_KR_SERVICE_KEY")
    if not env:
        return r

    base = "https://apis.data.go.kr/B552584/EvCharger"
    common = {"serviceKey": env["DATA_GO_KR_SERVICE_KEY"], "pageNo": 1, "numOfRows": 50,
              "zcode": CAMP_ZCODE, "dataType": "JSON"}

    resp, r.elapsed_ms, err = http_get(f"{base}/getChargerInfo", params=common)
    if err:
        r.code = err
        return r
    body = parse_body(resp)
    save_sample("charger_info", body, common)
    r.code = data_go_kr_error(body) or ("" if resp.ok else f"HTTP_{resp.status_code}")
    if r.code:
        return r

    items = find_items(body)
    r.ok = True
    r.note(f"getChargerInfo 받은 건수 {len(items)}, totalCount={find_value(body, 'totalCount')}")
    if items:
        r.note(f"필드: {', '.join(sorted(items[0].keys()))}")
        for it in items[:3]:
            r.note(f"  - {it.get('statNm')} | 타입 {it.get('chgerType')} | 출력 {it.get('output')} | "
                   f"상태 {it.get('stat')} | 좌표 ({it.get('lat')}, {it.get('lng')})")

    status_params = {**common, "period": 10}
    resp, _, err = http_get(f"{base}/getChargerStatus", params=status_params)
    if resp is not None:
        sbody = parse_body(resp)
        save_sample("charger_status", sbody, status_params)
        sitems = find_items(sbody)
        stats: dict[str, int] = {}
        for it in sitems:
            stats[str(it.get("stat"))] = stats.get(str(it.get("stat")), 0) + 1
        r.note(f"getChargerStatus(최근 10분 갱신) {len(sitems)}건, stat 값 분포 {stats}  ← 상태 코드 매핑 확인")
    else:
        r.note(f"getChargerStatus 호출 실패: {err}")
    return r


# ---------------------------------------------------------------- 3. Open-Meteo

def check_weather() -> CheckResult:
    r = CheckResult("Open-Meteo (기온 · 강수 예보)")
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": CAMP_LAT,
        "longitude": CAMP_LON,
        "hourly": "temperature_2m,precipitation_probability,precipitation,shortwave_radiation",
        "timezone": "Asia/Seoul",
        "forecast_days": 16,
    }
    resp, r.elapsed_ms, err = http_get(url, params=params)
    if err:
        r.code = err
        return r
    body = parse_body(resp)
    save_sample("weather_ok", body, params)
    if not resp.ok:
        r.code = f"HTTP_{resp.status_code}"
        r.note(str(body)[:200])
        return r

    hourly = body.get("hourly", {})
    times = hourly.get("time", [])
    temps = [t for t in hourly.get("temperature_2m", []) if t is not None]
    probs = [p for p in hourly.get("precipitation_probability", []) if p is not None]
    r.ok = bool(times)
    r.note(f"시간별 {len(times)}개, 범위 {times[0] if times else '-'} ~ {times[-1] if times else '-'}  ← 예보 제공 범위")
    if temps:
        r.note(f"기온 최저 {min(temps)}℃ / 최고 {max(temps)}℃, 강수확률 최대 {max(probs) if probs else '-'}%")
    null_probs = sum(1 for p in hourly.get("precipitation_probability", []) if p is None)
    r.note(f"강수확률 null 개수 {null_probs}  ← 먼 날짜에 값이 비는지 확인")

    # 예보 범위 밖 날짜
    far = dict(params)
    far.pop("forecast_days")
    far.update({"start_date": "2030-01-01", "end_date": "2030-01-02"})
    resp, _, err = http_get(url, params=far)
    if resp is not None:
        fbody = parse_body(resp)
        save_sample("weather_out_of_range", fbody, far)
        r.note(f"범위 밖 날짜 응답: HTTP {resp.status_code}, {str(fbody)[:120]}")
    return r


# ---------------------------------------------------------------- 4. 카카오 로컬 + 길찾기

ORIGIN_COORD: tuple[float, float] | None = None  # (lat, lon)


def check_kakao_local() -> CheckResult:
    global ORIGIN_COORD
    r = CheckResult("카카오 로컬 (주소 → 좌표)")
    env = require_env(r, "KAKAO_REST_API_KEY")
    if not env:
        return r
    headers = {"Authorization": f"KakaoAK {env['KAKAO_REST_API_KEY']}"}
    url = "https://dapi.kakao.com/v2/local/search/address.json"

    params = {"query": ORIGIN_ADDRESS}
    resp, r.elapsed_ms, err = http_get(url, params=params, headers=headers)
    if err:
        r.code = err
        return r
    body = parse_body(resp)
    save_sample("kakao_local_ok", body, params)
    if resp.status_code in (401, 403):
        r.code = f"AUTH_FAILED(HTTP {resp.status_code})"
        r.note(str(body)[:200] + "  ← 앱 설정에서 카카오맵(로컬) 사용 설정 확인")
        return r
    if not resp.ok:
        r.code = f"HTTP_{resp.status_code}"
        return r

    docs = body.get("documents", [])
    r.ok = bool(docs)
    if docs:
        ORIGIN_COORD = (float(docs[0]["y"]), float(docs[0]["x"]))
        r.note(f"'{ORIGIN_ADDRESS}' → ({ORIGIN_COORD[0]}, {ORIGIN_COORD[1]})")
    else:
        r.code = "NO_RESULT"

    params = {"query": "존재하지않는주소테스트123"}
    resp, _, _ = http_get(url, params=params, headers=headers)
    if resp is not None:
        ebody = parse_body(resp)
        save_sample("kakao_local_empty", ebody, params)
        r.note(f"0건 응답: HTTP {resp.status_code}, documents {len(ebody.get('documents', []))}건")
    return r


def check_route() -> CheckResult:
    r = CheckResult("카카오모빌리티 (자동차 길찾기)")
    env = require_env(r, "KAKAO_REST_API_KEY")
    if not env:
        return r
    origin = ORIGIN_COORD or (37.5663, 126.9779)  # 로컬 확인 실패 시 서울시청 좌표
    headers = {"Authorization": f"KakaoAK {env['KAKAO_REST_API_KEY']}"}
    url = "https://apis-navi.kakaomobility.com/v1/directions"

    params = {"origin": f"{origin[1]},{origin[0]}", "destination": f"{CAMP_LON},{CAMP_LAT}", "priority": "RECOMMEND"}
    resp, r.elapsed_ms, err = http_get(url, params=params, headers=headers)
    if err:
        r.code = err
        return r
    body = parse_body(resp)
    save_sample("route_ok", body, params)
    if resp.status_code in (401, 403):
        r.code = f"AUTH_FAILED(HTTP {resp.status_code})"
        r.note(str(body)[:200] + "  ← 길찾기 API 사용 권한 · 쿼터 확인")
        return r
    if not resp.ok:
        r.code = f"HTTP_{resp.status_code}"
        r.note(str(body)[:200])
        return r

    routes = body.get("routes", [])
    first = routes[0] if routes else {}
    if first.get("result_code") == 0:
        summary = first.get("summary", {})
        r.ok = True
        r.note(f"편도 {summary.get('distance', 0) / 1000:.1f}km, {summary.get('duration', 0) / 60:.0f}분")
    else:
        r.code = f"NO_ROUTE(result_code={first.get('result_code')}, {first.get('result_msg')})"

    # 경로가 없는 경우 (바다 한가운데 목적지)
    params = {"origin": f"{origin[1]},{origin[0]}", "destination": "125.0,35.0"}
    resp, _, _ = http_get(url, params=params, headers=headers)
    if resp is not None:
        nbody = parse_body(resp)
        save_sample("route_no_route", nbody, params)
        nr = (nbody.get("routes") or [{}])[0]
        r.note(f"경로 없음 응답: HTTP {resp.status_code}, result_code={nr.get('result_code')}, {nr.get('result_msg')}")
    return r


# ---------------------------------------------------------------- 5. LLM

def check_llm() -> CheckResult:
    r = CheckResult("LLM (호출 · Tool 선택)")
    env = require_env(r, "OPENAI_API_KEY", "LLM_MODEL")
    if not env:
        return r
    try:
        from openai import OpenAI
    except ImportError:
        r.code = "MISSING_PACKAGE(openai)"
        return r

    client = OpenAI(api_key=env["OPENAI_API_KEY"], base_url=os.getenv("OPENAI_BASE_URL") or None, timeout=30)
    model = env["LLM_MODEL"]
    started = time.perf_counter()
    try:
        # Chat Completions는 추론형 모델에서 function tool을 막는 경우가 있어 Responses API를 쓴다.
        ping = client.responses.create(model=model, input="연결 확인입니다. '확인'이라고만 답하세요.")
        r.note(f"응답: {ping.output_text!r}")

        tools = [{
            "type": "function",
            "name": "search_campsites",
            "description": "사용자가 아직 캠핑장을 정하지 않았거나 다른 캠핑장 대안이 필요할 때, "
                           "지역명 또는 좌표 주변의 캠핑장 후보를 찾는다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "지역명"},
                    "require_electric": {"type": "boolean", "description": "전기 사용 가능 캠핑장만"},
                },
                "required": ["region"],
            },
        }]
        tc = client.responses.create(
            model=model,
            input=[{"role": "user", "content": "이번 주말 가평에 전기 되는 캠핑장으로 차박 가고 싶어"}],
            tools=tools,
        )
        calls = [o for o in tc.output if getattr(o, "type", "") == "function_call"]
        r.elapsed_ms = int((time.perf_counter() - started) * 1000)
        if calls:
            r.ok = True
            r.note(f"Tool 선택: {calls[0].name}({calls[0].arguments})")
        else:
            r.code = "NO_TOOL_CALL"
            r.note("Tool을 선택하지 않았습니다 → 설명 문구 · 모델 확인")
    except Exception as exc:  # noqa: BLE001 - 연결 확인용이므로 어떤 오류든 기록
        r.elapsed_ms = int((time.perf_counter() - started) * 1000)
        r.code = f"LLM_ERROR({type(exc).__name__})"
        body = getattr(getattr(exc, "response", None), "text", "") or ""
        r.note((body or str(exc))[:300])  # 상태 코드만으로는 원인을 알 수 없어 응답 본문을 남긴다
    return r


# ---------------------------------------------------------------- 실행

CHECKS = {
    "campsite": check_campsite,
    "charger": check_charger,
    "weather": check_weather,
    "local": check_kakao_local,
    "route": check_route,
    "llm": check_llm,
}


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
