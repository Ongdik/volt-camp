"""실행 로그 이상 징후 판정 (규칙만, LLM 없음).

판정 결과
  WARN         규칙에 걸린 항목이 하나 이상
  OK           턴의 시작 · 끝 이벤트가 모두 있고 걸린 항목 없음
  UNDECIDABLE  근거가 될 이벤트가 부족해 판정할 수 없음 (이상 없음과 다르다)
"""

from __future__ import annotations

import json
from typing import Any

# 계획에 있던 Tool에서 실행 중에 이어질 수 있는 Tool (앞 결과 → 다음 입력)
FOLLOW_UPS = {"search_campsites": {"get_route"}, "search_chargers": {"get_route"}}
STRATEGY_TOOLS = {"ADD_CHARGE_STOP": {"search_chargers", "get_route"},
                  "USE_ELECTRIC_SITE": {"search_campsites", "get_route"},
                  "EXPAND_RADIUS": {"search_campsites", "get_route"}}


def _payload(e: dict[str, Any]) -> dict[str, Any]:
    return e.get("payload") or {}


def judge_turn(events: list[dict[str, Any]], max_replan: int = 3) -> dict[str, Any]:
    types = [e["type"] for e in events]
    if "TURN_START" not in types or "TURN_END" not in types:
        return {"verdict": "UNDECIDABLE", "findings": [], "reason": "턴의 시작 · 끝 이벤트가 없어 판정할 수 없습니다"}

    findings: list[dict[str, Any]] = []
    calls = [e for e in events if e["type"] == "TOOL_CALL"]
    results = {_payload(e).get("step_id") for e in events if e["type"] == "TOOL_RESULT"}

    # ① 같은 Tool · 같은 인자를 캐시 없이 두 번 이상
    seen: dict[str, list[int]] = {}
    for e in calls:
        p = _payload(e)
        if p.get("from_cache"):
            continue
        key = p.get("tool", "") + json.dumps(p.get("args"), sort_keys=True, ensure_ascii=False)
        seen.setdefault(key, []).append(e["seq"])
    for key, seqs in seen.items():
        if len(seqs) > 1:
            findings.append({"rule": "REPEATED_CALL", "evidence_seq": seqs,
                             "detail": f"{key.split('{')[0]}을(를) 같은 인자로 {len(seqs)}번 호출"})

    # ② 재계획 과다 (상한 도달)
    replans = [e for e in events if e["type"] == "REPLAN"]
    if len(replans) >= max_replan:
        findings.append({"rule": "EXCESSIVE_REPLAN", "evidence_seq": [e["seq"] for e in replans],
                         "detail": f"재계획 {len(replans)}회로 상한({max_replan})에 도달"})

    # ③ 계획에 없던 Tool
    allowed: set[str] = set()
    for e in events:
        p = _payload(e)
        if e["type"] == "LLM_DECISION" and p.get("kind") == "plan":
            allowed |= set(p.get("decision") or [])
        if e["type"] == "REPLAN":
            allowed |= STRATEGY_TOOLS.get(p.get("strategy"), set())
    for tool in list(allowed):
        allowed |= FOLLOW_UPS.get(tool, set())
    for e in calls:
        tool = _payload(e).get("tool")
        if tool not in allowed:
            findings.append({"rule": "UNPLANNED_TOOL", "evidence_seq": [e["seq"]], "detail": f"계획에 없던 {tool} 호출"})

    # ④ 응답이 없는 Tool 호출
    for e in calls:
        if _payload(e).get("step_id") not in results:
            findings.append({"rule": "MISSING_RESULT", "evidence_seq": [e["seq"]],
                             "detail": f"{_payload(e).get('tool')} 호출에 대한 결과 이벤트가 없음"})

    return {"verdict": "WARN" if findings else "OK", "findings": findings, "reason": ""}
