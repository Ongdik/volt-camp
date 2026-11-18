"""실제 API · LLM으로 대표 요청을 돌려 결과를 api_samples/agent_runs.json에 저장한다 (검증 기록용).

사용 (backend/에서): python scripts/run_scenarios.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.factory import build_planner  # noqa: E402

SCENARIOS = [
    ("S1 한 번에 충족", ["내일 서울시청에서 출발해서 가평으로 1박 차박 가려고 해. 아이오닉5 77.4kWh에 지금 80%, "
                       "전기장판 밤새 쓸 거고 집에 오면 30%는 남아야 해."]),
    ("S2 배터리 부족", ["내일 서울시청에서 가평으로 1박 차박. 배터리 77.4kWh, 지금 45%야. "
                      "전기히터 6시간이랑 전기장판 8시간 쓸 거고 귀가할 때 30%는 남겨야 해. 17시까지 도착하고 싶어."]),
    ("S6 Tool 불필요", ["V2L이 뭐야?"]),
    ("S7 되묻기", ["주말에 차박 갈 건데 배터리 충분할까?"]),
    ("S8 후속 변경", ["내일 서울시청에서 가평으로 1박 차박, 77.4kWh 80%, 전기장판 8시간, 귀가 30%.",
                     "같은 조건으로 홍천은 어때?", "이걸로 저장해줘"]),
    ("사실 조회", ["가평 근처 급속 충전기 지금 비어 있어?"]),
]


def main() -> None:
    agent = build_planner()
    out = []
    for name, messages in SCENARIOS:
        session = f"demo-{int(time.time())}-{len(out)}"
        for msg in messages:
            r = agent.run(msg, session)
            short = {k: r.get(k) for k in ("status", "intent", "intent_reason", "changed", "missing", "replans",
                                          "assumptions", "tool_failures", "action", "elapsed_ms")}
            short["recommendations"] = [{"name": c["campsite"]["name"], "soc": c["soc"],
                                         "charge_stop": (c["charge_stop"] or {}).get("station", {}).get("name")}
                                        for c in r.get("recommendations", [])]
            short["tool_calls"] = [(e["payload"]["tool"], e["payload"]["from_cache"]) for e in r["timeline"]
                                   if e["type"] == "TOOL_CALL"]
            out.append({"scenario": name, "message": msg, "result": short, "answer": r["answer"]})
            print(f"\n=== {name} | {msg}\n[{r['status']}] {r.get('intent')} ({r['elapsed_ms']} ms)\n{r['answer'][:600]}")
    path = Path(__file__).resolve().parents[1] / "api_samples" / "agent_runs.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\n저장: {path}")


if __name__ == "__main__":
    main()
