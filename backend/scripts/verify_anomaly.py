"""이상 징후 판단(F3020) 검증: 이상 실행을 실제로 만들어 로깅 Agent가 식별하는지 확인한다.

만드는 실행 (세션 이름이 check- 로 시작해 실행 로그 화면에서 구분된다)
  check-normal      정상 차박 계획            → OK, LLM 진단 0회
  check-repeat      버그 주입: 성공한 날씨 조회를 캐시 없이 다시 호출 → REPEATED_CALL + LLM 원인 진단
  check-replan      조건을 못 맞춰 재계획을 상한까지 반복 (실제 API) → EXCESSIVE_REPLAN + LLM 원인 진단
  check-unplanned   버그 주입: 계획 결정에 없는 Tool을 실행     → UNPLANNED_TOOL + LLM 원인 진단
  check-undecid     로그 저장 실패 흉내: 턴 시작 이벤트 유실   → UNDECIDABLE (이상 없음 아님), LLM 0회
  check-repeat 2턴  같은 세션 다음 턴에서 메인 Agent가 직전 경고를 참조(previous_warning)하는지

요청 분석은 정해 둔 값으로 대신해(결과를 매번 같게) 외부 API · 판정 · 진단만 실제로 돈다.
실행 (backend/에서): python scripts/verify_anomaly.py   → data/verify_anomaly.json
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.factory import default_registry  # noqa: E402
from app.agent.graph import PlannerAgent  # noqa: E402
from app.agent.llm import make_diagnoser  # noqa: E402
from app.agent.memory import MemoryStore  # noqa: E402
from app.agent.state import Analysis  # noqa: E402
from app.clients import kakao  # noqa: E402
from app.clients.kma import KST  # noqa: E402
from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.db.anomalies import AnomalyRepository  # noqa: E402
from app.db.database import Database  # noqa: E402
from app.db.run_logs import RunLogRepository  # noqa: E402
from app.events.bus import EventBus  # noqa: E402
from app.events.types import EventType  # noqa: E402
from app.logging_agent.agent import LoggingAgent  # noqa: E402

SESSIONS = ["check-normal", "check-repeat", "check-replan", "check-unplanned", "check-undecid"]


def script(start: date) -> dict[str, dict[str, Any]]:
    trip = {"intent": "PLAN_TRIP", "intent_reason": "날짜 · 출발지 · 지역 · 차량 · 기기가 있음",
            "start_date": start, "nights": 1, "origin": "서울시청", "region": "가평", "capacity_kwh": 77.4,
            "soc_start_pct": 80, "devices": [{"name": "전기장판", "hours": 10}]}
    return {
        "정상 계획": trip,
        "강릉 무리한 계획": {**trip, "region": "강릉", "capacity_kwh": 64, "soc_start_pct": 30, "min_return_pct": 50,
                        "intent_reason": "잔량이 적은데 먼 곳 · 높은 귀가 잔량"},
        "가평 캠핑장 알려줘": {"intent": "FACT_QUERY", "intent_reason": "캠핑장만 조회", "region": "가평",
                         "fact_target": "campsites"},
        "안녕": {"intent": "CHITCHAT", "intent_reason": "인사"},
    }


class ScriptedLLM:
    """요청 분석은 정해 둔 값, 답변 문장은 코드의 기본 문장 (검증 대상이 아닌 LLM 호출을 줄인다)."""

    def __init__(self, table: dict[str, dict[str, Any]]):
        self.table = table

    def analyze(self, message: str, memory_summary: str, today: date) -> Analysis:
        return Analysis(**self.table[message])

    def write(self, kind: str, context: dict[str, Any]) -> str:
        return ""


class RetryBugAgent(PlannerAgent):
    """버그 주입: 성공한 날씨 조회를 캐시를 무시하고 한 번 더 부른다 (잘못된 재시도 로직)."""

    def _call(self, state, em, step, cache):
        result = super()._call(state, em, step, cache)
        if step["tool"] == "get_weather" and result["ok"]:
            result = super()._call(state, em, step, {})
        return result


class HiddenStepAgent(PlannerAgent):
    """버그 주입: 계획 결정(LLM_DECISION)에 알리지 않은 충전소 조회를 끼워 넣는다."""

    def plan(self, state, em):
        out = super().plan(state, em)
        center = state["places"].get("region") or state["places"].get("origin")
        out["plan"].append({"step_id": "hidden", "tool": "search_chargers", "purpose": "선언하지 않은 단계",
                            "args": {"lat": center["lat"], "lon": center["lon"], "fast_only": True}, "fixed": []})
        return out


class DropStartRepo(RunLogRepository):
    """로그 저장 실패 흉내: 턴 시작 이벤트가 저장되지 않는다."""

    def append(self, event):
        if event.type is EventType.TURN_START:
            return
        super().append(event)


class CountingDiagnoser:
    def __init__(self, inner):
        self.inner = inner
        self.calls: list[str] = []

    def __call__(self, events, findings):
        self.calls.append(events[0]["session_id"] if events else "?")
        return self.inner(events, findings)


def main() -> int:
    s = get_settings()
    db = Database(s.db_path)
    logs, anomalies = RunLogRepository(db), AnomalyRepository(db)
    for sid in SESSIONS:
        logs.delete_session(sid)
        anomalies.delete_session(sid)

    inner = make_diagnoser()
    if inner is None:
        print("LLM 키가 없어 원인 진단은 건너뜁니다 (판정만 확인)")
    diagnoser = CountingDiagnoser(inner) if inner else None

    bus, broken_bus = EventBus(), EventBus()
    LoggingAgent(logs, anomalies, diagnoser=diagnoser, max_replan=s.max_replan, background=False).attach(bus)
    LoggingAgent(DropStartRepo(db), anomalies, diagnoser=diagnoser, max_replan=s.max_replan,
                 background=False).attach(broken_bus)

    today = datetime.now(KST).date()
    table = script(today + timedelta(days=1))
    memory = MemoryStore()
    registry = default_registry()     # 외부 API Tool 4종 (캘린더는 별도 검증)

    def agent(cls=PlannerAgent, on_bus=bus):
        a = cls(registry, lambda: ScriptedLLM(table), kakao.geocode, bus=on_bus, memory=memory,
                max_replan=s.max_replan, today=lambda: today)
        a.warning_lookup = anomalies.latest_warning
        return a

    runs = [
        ("check-normal", agent(), "정상 계획", "OK"),
        ("check-repeat", agent(RetryBugAgent), "정상 계획", "WARN:REPEATED_CALL"),
        ("check-replan", agent(), "강릉 무리한 계획", "WARN:EXCESSIVE_REPLAN"),
        ("check-unplanned", agent(HiddenStepAgent), "가평 캠핑장 알려줘", "WARN:UNPLANNED_TOOL"),
        ("check-undecid", agent(on_bus=broken_bus), "안녕", "UNDECIDABLE"),
    ]
    report: dict[str, Any] = {"checked_at": datetime.now(KST).isoformat(timespec="seconds"), "runs": []}
    for sid, a, message, expected in runs:
        before = len(diagnoser.calls) if diagnoser else 0
        r = a.run(message, sid)
        [verdict] = [x for x in anomalies.for_session(sid) if x["turn_id"] == r["turn_id"]]
        rules = sorted({f["rule"] for f in verdict["findings"]})
        got = verdict["verdict"] + (":" + ",".join(rules) if rules else "")
        llm_calls = (len(diagnoser.calls) - before) if diagnoser else 0
        row = {"session": sid, "message": message, "expected": expected, "verdict": verdict["verdict"],
               "rules": rules, "findings": verdict["findings"], "diagnosis_status": verdict["diagnosis_status"],
               "diagnosis": verdict["diagnosis"], "diagnosis_llm_calls": llm_calls,
               "replans": len(r.get("replans", [])), "agent_status": r["status"],
               "pass": expected.split(":")[0] == verdict["verdict"] and all(
                   rule in rules for rule in expected.split(":")[1:2] if rule)
               and (llm_calls == 0 if verdict["verdict"] != "WARN" else (llm_calls == 1 or diagnoser is None))}
        report["runs"].append(row)
        print(f"[{'PASS' if row['pass'] else 'FAIL'}] {sid:<16} 기대 {expected:<24} 판정 {got:<28} 진단 LLM {llm_calls}회")
        if verdict["diagnosis"]:
            print(f"    진단: {verdict['diagnosis']}")

    # 같은 세션 다음 턴: 메인 Agent가 직전 경고를 참조하는가
    follow = agent().run("안녕", "check-repeat")
    ref = follow.get("previous_warning")
    ok = bool(ref) and any(f["rule"] == "REPEATED_CALL" for f in ref["findings"])
    report["main_agent_reference"] = {"session": "check-repeat", "previous_warning": ref, "pass": ok}
    print(f"[{'PASS' if ok else 'FAIL'}] 메인 Agent 참조   다음 턴 응답의 previous_warning: "
          f"{[f['rule'] for f in ref['findings']] if ref else None}")

    report["all_pass"] = all(x["pass"] for x in report["runs"]) and ok
    out = BACKEND_DIR / "data" / "verify_anomaly.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\n결과: {'모두 통과' if report['all_pass'] else '실패 있음'} → {out}")
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
