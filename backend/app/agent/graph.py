"""메인 Agent 실행 흐름 (LangGraph).

  analyze → plan → execute → judge ─┬─→ respond
     │                    ▲          └─→ replan ─┘ (충족 후보가 없고 재계획할 전략이 남았을 때, 상한 MAX_REPLAN)
     └─(되묻기 · 대화 · 저장)→ respond

LLM은 analyze(의도 · 조건 추출)와 respond(계산 결과를 문장으로)에서만 쓴다.
계획 · 재계획은 규칙, 판단은 계산(energy.py)이라 같은 입력이면 같은 흐름이 나온다.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.agent import energy
from app.agent.devices import DEVICE_CATALOG, Vehicle, make_device
from app.agent.llm import AgentLLM
from app.agent.memory import MemoryStore
from app.agent.prompts import SAFETY_NOTICE
from app.agent.state import AgentState, Conditions
from app.clients.kma import KST
from app.events.bus import EventBus, RunEmitter
from app.events.types import EventType
from app.tools.base import FATAL_CODES, ErrorCode, ToolFailure, ToolResult
from app.tools.registry import ToolRegistry

DEFAULT_RADIUS_KM = 10
MAX_RADIUS_KM = 20
CANDIDATES_TO_CHECK = 5
CHARGE_SOC_CAP = 0.80          # 급속충전은 80% 이후 급격히 느려져 그 위로는 계획하지 않는다
DEFAULT_DEPART = "10:00"
DEFAULT_RETURN = "14:00"

Geocoder = Callable[[str], Any]


def _emitter(config: RunnableConfig) -> RunEmitter:
    return config["configurable"]["emitter"]


def _ok(result: dict[str, Any] | None) -> bool:
    return bool(result) and result.get("ok") is True


class PlannerAgent:
    def __init__(self, registry: ToolRegistry, llm_factory: Callable[[], AgentLLM], geocode: Geocoder,
                 bus: EventBus | None = None, memory: MemoryStore | None = None, max_replan: int = 3,
                 today: Callable[[], date] | None = None):
        self.registry = registry
        self._llm_factory = llm_factory
        self._llm: AgentLLM | None = None
        self.geocode = geocode
        self.bus = bus
        self.memory = memory or MemoryStore()
        self.max_replan = max_replan
        self.today = today or (lambda: datetime.now(KST).date())
        self.graph = self._build()

    # ------------------------------------------------------------------ 공통

    @property
    def llm(self) -> AgentLLM:
        if self._llm is None:
            self._llm = self._llm_factory()      # 키가 없으면 ToolFailure(MISSING_API_KEY)
        return self._llm

    def _node(self, name: str, fn: Callable[[AgentState, RunEmitter], dict[str, Any]]):
        def wrapped(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
            em = _emitter(config)
            em.emit(EventType.NODE_ENTER, node=name)
            started = time.perf_counter()
            update = fn(state, em)
            em.emit(EventType.NODE_EXIT, node=name, elapsed_ms=int((time.perf_counter() - started) * 1000),
                    summary=self._exit_summary(name, update))
            return update
        return wrapped

    @staticmethod
    def _exit_summary(name: str, update: dict[str, Any]) -> dict[str, Any]:
        if name == "analyze":
            return {k: update.get(k) for k in ("intent", "status", "missing", "changed") if update.get(k)}
        if name == "plan" or name == "replan":
            return {"steps": [f"{s['tool']}({s['purpose']})" for s in update.get("plan", [])][-6:],
                    "stop_reason": update.get("stop_reason")}
        if name == "judge":
            return {"evaluations": [(e["campsite"]["name"], e["status"]) for e in update.get("evaluations", [])]}
        return {"status": update.get("status")} if update.get("status") else {}

    def _call(self, state: AgentState, em: RunEmitter, step: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
        """Tool 호출. 같은 Tool · 같은 인자는 캐시에서 꺼낸다 (재계획 때 다시 부르지 않기)."""
        key = step["tool"] + json.dumps(step["args"], sort_keys=True, ensure_ascii=False, default=str)
        cached = key in cache
        em.emit(EventType.TOOL_CALL, node="execute", step_id=step["step_id"], tool=step["tool"],
                args=step["args"], purpose=step["purpose"], from_cache=cached)
        result = cache[key] if cached else self.registry.invoke(step["tool"], step["args"]).model_dump(mode="json")
        cache[key] = result
        em.emit(EventType.TOOL_RESULT, node="execute", step_id=step["step_id"], tool=step["tool"],
                ok=result["ok"], error_code=(result.get("error") or {}).get("code"),
                elapsed_ms=result["meta"]["elapsed_ms"], from_cache=cached, summary=self._result_summary(result))
        return result

    @staticmethod
    def _result_summary(result: dict[str, Any]) -> str:
        if not result["ok"]:
            return result["error"]["message"]
        d = result["data"]
        if "candidates" in d:
            return f"캠핑장 {len(d['candidates'])}곳"
        if "distance_km" in d:
            return f"{d['distance_km']}km · {d['duration_min']}분"
        if "summary" in d:
            s = d["summary"]
            return f"최저 {s['min_temp_c']}℃, 강수확률 최대 {s['max_precip_prob']}%"
        if "stations" in d:
            return f"충전소 {d['station_count']}곳"
        return "ok"

    # ------------------------------------------------------------------ analyze

    def analyze(self, state: AgentState, em: RunEmitter) -> dict[str, Any]:
        mem = self.memory.get(state["session_id"])
        summary = json.dumps({"conditions": mem.conditions.model_dump(mode="json", exclude_none=True),
                              "last_recommendations": mem.last_summary}, ensure_ascii=False)
        try:
            analysis = self.llm.analyze(state["message"], summary, self.today())
        except ToolFailure as f:
            return {"status": "ERROR", "stop_reason": f.code.value, "answer": f.message}
        except Exception:  # noqa: BLE001
            return {"status": "ERROR", "stop_reason": "LLM_ERROR",
                    "answer": "요청을 분석하는 중 문제가 생겼어요. 잠시 후 다시 말씀해 주세요."}
        em.emit(EventType.LLM_DECISION, node="analyze", kind="intent", decision=analysis.intent,
                reason=analysis.intent_reason)

        conditions, changed = mem.conditions.merge(analysis)
        mem.conditions = conditions
        update: dict[str, Any] = {"intent": analysis.intent, "intent_reason": analysis.intent_reason,
                                  "fact_target": analysis.fact_target, "conditions": conditions, "changed": changed}
        intent = "PLAN_TRIP" if analysis.intent == "MODIFY" else analysis.intent
        update["intent"] = intent
        if intent in ("CHITCHAT", "CONFIRM_PLAN"):
            return update

        missing = conditions.missing_for_plan() if intent == "PLAN_TRIP" else (
            [] if (conditions.region or conditions.origin) else ["어느 지역인지"])
        if intent == "PLAN_TRIP" and conditions.devices:
            missing += [f"{d.name} 소비전력(W)" for d in conditions.devices
                        if d.power_w is None and d.name.replace(" ", "") not in DEVICE_CATALOG]
        if missing:
            return {**update, "status": "NEED_INFO", "missing": missing}

        places: dict[str, Any] = {}
        for key in (("origin", "region") if intent == "PLAN_TRIP" else ("region",)):
            text = getattr(conditions, key) or (conditions.origin if key == "region" else None)
            if not text:
                continue
            try:
                p = self.geocode(text)
                places[key] = {"query": text, "address": p.address, "lat": p.lat, "lon": p.lon}
            except ToolFailure as f:
                if f.code in FATAL_CODES:
                    return {**update, "status": "ERROR", "stop_reason": f.code.value, "answer": f.message}
                return {**update, "status": "NEED_INFO", "missing": [f"'{text}'의 정확한 위치 (주소나 다른 지명)"]}
        return {**update, "places": places}

    # ------------------------------------------------------------------ plan

    def plan(self, state: AgentState, em: RunEmitter) -> dict[str, Any]:
        c: Conditions = state["conditions"]
        center = state["places"].get("region") or state["places"].get("origin")
        at = {"lat": center["lat"], "lon": center["lon"]}
        steps: list[dict[str, Any]] = []
        if state["intent"] == "PLAN_TRIP":
            steps.append({"step_id": "s1", "tool": "search_campsites", "purpose": "캠핑장 후보",
                          "args": {**at, "radius_km": DEFAULT_RADIUS_KM, "require_electric": bool(c.prefer_electric),
                                   "limit": CANDIDATES_TO_CHECK},
                          "fixed": ["lat", "lon"]})
            steps.append({"step_id": "s2", "tool": "get_weather", "purpose": "기온 · 강수 (지역 중심)",
                          "args": {**at, "start_date": c.start_date.isoformat(), "end_date": c.end_date.isoformat()},
                          "fixed": ["start_date", "end_date"]})
            reason = "캠핑장을 정하지 않아 후보 조회 → 후보별 길찾기 → 지역 날씨 순으로 계획"
        else:
            target = state.get("fact_target") or "campsites"
            start = c.start_date or self.today()
            tool, args = {
                "campsites": ("search_campsites", {**at, "radius_km": DEFAULT_RADIUS_KM,
                                                   "require_electric": bool(c.prefer_electric)}),
                "chargers": ("search_chargers", {**at, "fast_only": c.fast_only is not False}),
                "weather": ("get_weather", {**at, "start_date": start.isoformat(),
                                            "end_date": (c.end_date or start + timedelta(days=1)).isoformat()}),
            }[target]
            steps.append({"step_id": "s1", "tool": tool, "purpose": f"사실 조회: {target}", "args": args, "fixed": []})
            reason = f"사실 조회({target})라 Tool 하나만 호출"
        em.emit(EventType.LLM_DECISION, node="plan", kind="plan", decision=[s["tool"] for s in steps],
                reason=reason, by="rule")
        return {"plan": steps, "executed": [], "results": {}, "replan_count": 0, "strategies_used": [],
                "replans": []}

    # ------------------------------------------------------------------ execute

    def execute(self, state: AgentState, em: RunEmitter) -> dict[str, Any]:
        plan, executed, results = list(state["plan"]), list(state["executed"]), dict(state["results"])
        cache = state.get("cache_ref") or {}
        origin = state["places"].get("origin")
        i = 0
        while i < len(plan):
            step = plan[i]
            i += 1
            if step["step_id"] in executed:
                continue
            result = self._call(state, em, step, cache)
            results[step["step_id"]] = result
            executed.append(step["step_id"])
            if not result["ok"]:
                continue
            # 앞 Tool 결과 → 다음 Tool 입력
            if step["tool"] == "search_campsites" and state["intent"] == "PLAN_TRIP" and origin:
                for cand in result["data"]["candidates"]:
                    sid = f"route:{cand['campsite_id']}"
                    if all(s["step_id"] != sid for s in plan):
                        plan.append({"step_id": sid, "tool": "get_route", "purpose": f"길찾기 {cand['name']}",
                                     "args": {"origin": {"lat": origin["lat"], "lon": origin["lon"]},
                                              "destination": {"lat": cand["lat"], "lon": cand["lon"]},
                                              "ref_id": cand["campsite_id"]}, "fixed": ["origin"]})
            if step["tool"] == "search_chargers" and step.get("for_campsite"):
                cid = step["for_campsite"]
                stations = result["data"]["stations"]
                station = next((s for s in stations if s["available_count"] > 0), stations[0])
                cand = self._candidate(results, cid)
                plan.append({"step_id": f"route_via:{cid}", "tool": "get_route",
                             "purpose": f"충전 경유 길찾기 {station['name']}",
                             "args": {"origin": {"lat": origin["lat"], "lon": origin["lon"]},
                                      "destination": {"lat": cand["lat"], "lon": cand["lon"]},
                                      "waypoint": {"lat": station["lat"], "lon": station["lon"]},
                                      "ref_id": cid},
                             "station": station, "fixed": ["origin"]})
        return {"plan": plan, "executed": executed, "results": results}

    @staticmethod
    def _candidates(results: dict[str, Any]) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for sid, r in results.items():
            if sid.startswith("s") and _ok(r) and "candidates" in r["data"]:
                for c in r["data"]["candidates"]:
                    seen.setdefault(c["campsite_id"], c)
        return list(seen.values())

    def _candidate(self, results: dict[str, Any], cid: str) -> dict[str, Any]:
        return next(c for c in self._candidates(results) if c["campsite_id"] == cid)

    # ------------------------------------------------------------------ judge

    def _vehicle(self, c: Conditions) -> Vehicle:
        kwargs: dict[str, Any] = {"capacity_kwh": c.capacity_kwh, "soc_start": c.soc_start_pct / 100}
        if c.efficiency_km_per_kwh:
            kwargs["efficiency_km_per_kwh"] = c.efficiency_km_per_kwh
        if c.min_return_pct is not None:
            kwargs["min_return"] = c.min_return_pct / 100
        return Vehicle(**kwargs)

    def judge(self, state: AgentState, em: RunEmitter) -> dict[str, Any]:
        c: Conditions = state["conditions"]
        results = state["results"]
        vehicle = self._vehicle(c)
        devices = [make_device(d.name, d.hours, d.power_w) for d in c.devices]
        weather = results.get("s2")
        weather_summary = weather["data"]["summary"] if _ok(weather) else None
        plan_by_id = {s["step_id"]: s for s in state["plan"]}

        evaluations = []
        for cand in self._candidates(results):
            cid = cand["campsite_id"]
            route, via = results.get(f"route:{cid}"), results.get(f"route_via:{cid}")
            base = {"campsite": cand, "route": route["data"] if _ok(route) else None,
                    "evidence": [s for s in ("s1", "s2", f"route:{cid}") if s in results]}
            if not _ok(route):
                reason = route["error"]["message"] if route else "경로를 확인하지 못했습니다"
                evaluations.append({**base, "status": "UNJUDGED", "failed": [], "unjudged": ["ENERGY"],
                                    "reasons": [reason], "metrics": {}, "deficit_kwh": 0, "charge_stop": None})
                continue
            ev = energy.evaluate(vehicle, devices, route["data"]["distance_km"], weather_summary,
                                 cand["has_electric"])
            charge_stop = None
            if ev.failed == ["ENERGY"] and _ok(via):
                station = plan_by_id[f"route_via:{cid}"]["station"]
                legs = via["data"]["legs"]
                to_charger_km = legs[0]["distance_km"] if legs else via["data"]["distance_km"] / 2
                probe = energy.evaluate(vehicle, devices, via["data"]["distance_km"], weather_summary,
                                        cand["has_electric"], return_distance_km=route["data"]["distance_km"],
                                        charge_after_km=to_charger_km)
                soc_at = probe.metrics["soc"]["at_charger"]
                room = max((CHARGE_SOC_CAP - soc_at) * vehicle.capacity_kwh, 0)
                charge_kwh = round(min(probe.deficit_kwh + 2.0, room), 1)
                ev = energy.evaluate(vehicle, devices, via["data"]["distance_km"], weather_summary,
                                     cand["has_electric"], charge_kwh=charge_kwh,
                                     return_distance_km=route["data"]["distance_km"], charge_after_km=to_charger_km)
                minutes = energy.charge_minutes(charge_kwh, station["max_output_kw"])
                latest = None
                if c.arrival_time and len(legs) > 1:
                    arrive = datetime.combine(c.start_date, datetime.strptime(c.arrival_time, "%H:%M").time())
                    latest = energy.latest_charger_arrival(arrive, minutes, legs[1]["duration_min"]).strftime("%H:%M")
                charge_stop = {"station": {k: station[k] for k in ("station_id", "name", "address", "lat", "lon",
                                                                     "max_output_kw", "available_count")},
                               "charge_kwh": charge_kwh, "charge_minutes": minutes, "latest_arrival": latest,
                               "soc_at_charger": soc_at, "route": via["data"]}
                base["evidence"] += [s for s in (f"chargers:{cid}", f"route_via:{cid}") if s in results]
                if charge_kwh < probe.deficit_kwh:
                    ev.reasons.append(f"충전소에서 {CHARGE_SOC_CAP:.0%}까지 충전해도 {probe.deficit_kwh}kWh가 모자랍니다")
            evaluations.append({**base, **ev.model_dump(), "charge_stop": charge_stop})

        em.emit(EventType.JUDGE_RESULT, node="judge", results=[
            {"campsite": e["campsite"]["name"], "status": e["status"], "failed": e["failed"],
             "soc": e["metrics"].get("soc"), "deficit_kwh": e["deficit_kwh"],
             "charge_stop": (e["charge_stop"] or {}).get("station", {}).get("name")} for e in evaluations])
        return {"evaluations": evaluations}

    # ------------------------------------------------------------------ replan

    def _next_strategy(self, state: AgentState) -> tuple[str | None, str]:
        """재계획 전략과 이유. 전략이 없으면 (None, 멈추는 이유)."""
        evals, used = state.get("evaluations", []), state.get("strategies_used", [])
        if any(e["status"] == "OK" for e in evals):
            return None, "충족 후보 있음"
        s1 = state["results"].get("s1")
        if s1 and not s1["ok"] and s1["error"]["code"] in {c.value for c in FATAL_CODES}:
            return None, f"재시도해도 같은 실패: {s1['error']['code']}"
        failed = {f for e in evals for f in e["failed"]}
        c: Conditions = state["conditions"]
        searches = [s for s in state["plan"] if s["tool"] == "search_campsites"]
        last = searches[-1]["args"] if searches else {}
        if evals and failed == {"POWER"}:
            return None, "출력 초과는 다른 캠핑장 · 충전소로 해결되지 않음"
        energy_only = [e for e in evals if e["failed"] == ["ENERGY"] and not e["charge_stop"]]
        if energy_only and c.allow_charging is not False and "ADD_CHARGE_STOP" not in used:
            return "ADD_CHARGE_STOP", "배터리만 모자란 후보가 있어 가는 길 충전을 넣어 본다"
        if failed & {"RAIN", "ENERGY"} and not last.get("require_electric") and "USE_ELECTRIC_SITE" not in used:
            return "USE_ELECTRIC_SITE", "비 · 배터리 문제를 전기 사이트(캠핑장 전기)로 피해 본다"
        if last.get("radius_km", MAX_RADIUS_KM) < MAX_RADIUS_KM and "EXPAND_RADIUS" not in used:
            return "EXPAND_RADIUS", f"탐색 반경을 {MAX_RADIUS_KM}km로 넓혀 본다"
        return None, "남은 재계획 전략 없음"

    def route_after_judge(self, state: AgentState) -> str:
        strategy, _ = self._next_strategy(state)
        if strategy and state.get("replan_count", 0) < self.max_replan:
            return "replan"
        return "respond"

    def replan(self, state: AgentState, em: RunEmitter) -> dict[str, Any]:
        strategy, reason = self._next_strategy(state)
        plan = list(state["plan"])
        count = state.get("replan_count", 0) + 1
        searches = [s for s in plan if s["tool"] == "search_campsites"]
        last = dict(searches[-1]["args"])
        if strategy == "ADD_CHARGE_STOP":
            targets = sorted((e for e in state["evaluations"] if e["failed"] == ["ENERGY"] and not e["charge_stop"]),
                             key=lambda e: e["deficit_kwh"])[:2]
            for e in targets:
                cand = e["campsite"]
                plan.append({"step_id": f"chargers:{cand['campsite_id']}", "tool": "search_chargers",
                             "purpose": f"충전소 {cand['name']} 주변", "for_campsite": cand["campsite_id"],
                             "args": {"lat": cand["lat"], "lon": cand["lon"], "radius_km": 15, "fast_only": True},
                             "fixed": []})
        elif strategy == "USE_ELECTRIC_SITE":
            plan.append({"step_id": f"s1_r{count}", "tool": "search_campsites", "purpose": "전기 사이트 캠핑장",
                         "args": {**last, "require_electric": True}, "fixed": ["lat", "lon"]})
        elif strategy == "EXPAND_RADIUS":
            plan.append({"step_id": f"s1_r{count}", "tool": "search_campsites", "purpose": "반경 확대",
                         "args": {**last, "radius_km": MAX_RADIUS_KM}, "fixed": ["lat", "lon"]})
        failed = sorted({f for e in state.get("evaluations", []) for f in e["failed"]})
        record = {"count": count, "strategy": strategy, "reason": reason, "failed_constraints": failed}
        em.emit(EventType.REPLAN, node="replan", **record)
        return {"plan": plan, "replan_count": count, "strategies_used": [*state.get("strategies_used", []), strategy],
                "replans": [*state.get("replans", []), record]}

    # ------------------------------------------------------------------ respond

    def respond(self, state: AgentState, em: RunEmitter) -> dict[str, Any]:
        intent, status = state.get("intent"), state.get("status")
        mem = self.memory.get(state["session_id"])
        out: dict[str, Any] = {"intent": intent, "intent_reason": state.get("intent_reason"),
                               "conditions": state["conditions"].model_dump(mode="json", exclude_none=True)
                               if state.get("conditions") else {},
                               "changed": state.get("changed", []), "replans": state.get("replans", []),
                               "notices": [], "recommendations": [], "excluded": [], "assumptions": [],
                               "missing": state.get("missing", []), "plan_draft": None, "action": None}
        if status == "ERROR":
            return {"status": "ERROR", "answer": state.get("answer") or "처리 중 오류가 생겼어요.",
                    "output": {**out, "error_code": state.get("stop_reason")}}
        if status == "NEED_INFO":
            answer = "계산하려면 다음 정보가 더 필요해요.\n" + "\n".join(f"- {m}" for m in state["missing"])
            return {"status": "NEED_INFO", "answer": answer, "output": out}
        if intent == "CONFIRM_PLAN":
            if not mem.plan_draft:
                return {"status": "NEED_INFO", "answer": "저장할 차박 계획이 아직 없어요. 먼저 계획을 세워 볼까요?",
                        "output": out}
            return {"status": "OK", "answer": "추천한 계획을 저장할게요.",
                    "output": {**out, "action": "SAVE_PLAN", "plan_draft": mem.plan_draft}}
        if intent == "CHITCHAT":
            return {"status": "OK", "answer": self._write("chat", {"message": state["message"]},
                                                          "안녕하세요! 출발 날짜 · 출발지 · 캠핑 지역 · 배터리 용량과 잔량 · "
                                                          "쓸 기기를 알려주시면 차박 전력 계획을 세워 드릴게요."),
                    "output": out}
        if intent == "FACT_QUERY":
            r = state["results"].get("s1")
            ctx = {"question": state["message"], "ok": r["ok"], "data": r.get("data"),
                   "error": r.get("error"), "notes": r["meta"]["notes"]}
            fallback = r["error"]["message"] if not r["ok"] else json.dumps(r["data"], ensure_ascii=False)[:800]
            return {"status": "OK" if r["ok"] else "PARTIAL", "answer": self._write("fact", ctx, fallback),
                    "output": {**out, "fact": ctx}}
        return self._respond_plan(state, out, mem)

    def _respond_plan(self, state: AgentState, out: dict[str, Any], mem) -> dict[str, Any]:
        c: Conditions = state["conditions"]
        evals = state.get("evaluations", [])
        ok = sorted((e for e in evals if e["status"] == "OK"),
                    key=lambda e: (e["charge_stop"] is not None, -e["metrics"]["soc"]["return"]))
        out["recommendations"] = [self._card(e) for e in ok[:3]]
        out["excluded"] = [{"campsite_name": e["campsite"]["name"], "status": e["status"], "reasons": e["reasons"]}
                           for e in evals if e["status"] != "OK"]
        out["assumptions"] = self._assumptions(state, evals)
        out["notices"] = [SAFETY_NOTICE]
        tool_failures = [{"tool": r["meta"]["tool"], "code": r["error"]["code"], "message": r["error"]["message"]}
                         for r in state["results"].values() if not r["ok"]]
        out["tool_failures"] = tool_failures

        if ok:
            status = "OK"
            best = ok[0]
            out["plan_draft"] = self._draft(c, best)
            mem.plan_draft = out["plan_draft"]
        else:
            _, why = self._next_strategy(state)
            status = "LIMIT_REACHED" if state.get("replan_count", 0) >= self.max_replan else "NO_OPTION"
            out["stop_reason"] = why
            out["suggestions"] = self._suggestions(c, evals)
            mem.plan_draft = None
        mem.last_summary = [{"campsite_id": e["campsite"]["campsite_id"], "name": e["campsite"]["name"],
                             "status": e["status"], "soc": e["metrics"].get("soc")} for e in evals][:5]
        ctx = {"status": status, "conditions": out["conditions"], "recommendations": out["recommendations"],
               "excluded": out["excluded"], "assumptions": out["assumptions"], "replans": out["replans"],
               "suggestions": out.get("suggestions"), "stop_reason": out.get("stop_reason"),
               "tool_failures": tool_failures}
        return {"status": status, "answer": self._write("plan", ctx, self._fallback_plan_text(ctx)), "output": out}

    @staticmethod
    def _card(e: dict[str, Any]) -> dict[str, Any]:
        cand, m = e["campsite"], e["metrics"]
        return {"campsite": {k: cand[k] for k in ("campsite_id", "name", "address", "lat", "lon", "has_electric",
                                                  "reservation_url", "tel")},
                "route": {"distance_km": m["distance_km_oneway"], "duration_min": (e["route"] or {}).get("duration_min")},
                "soc": m["soc"], "limits": m["limits"], "e_drive_oneway_kwh": m["e_drive_oneway_kwh"],
                "e_camp_kwh": m["e_camp_kwh"], "temp_factor": m["temp_factor"],
                "rain_expected": m.get("rain_expected"), "charge_stop": e["charge_stop"],
                "unjudged": e["unjudged"], "evidence": e["evidence"]}

    def _assumptions(self, state: AgentState, evals: list[dict[str, Any]]) -> list[str]:
        c: Conditions = state["conditions"]
        out = []
        if not c.efficiency_km_per_kwh:
            out.append("전비 5.0km/kWh (기본값)")
        if c.min_return_pct is None:
            out.append("귀가 시 최소 잔량 20% (기본값)")
        out.append("V2L 방전 제한 20% · 최대 출력 3,500W (기본값)")
        out += [f"{d.name} {make_device(d.name, d.hours or 0).power_w}W (표준값)" for d in c.devices or []
                if d.power_w is None and d.name.replace(" ", "") in DEVICE_CATALOG]
        out.append("날씨는 캠핑 지역 중심 좌표의 예보")
        for r in state["results"].values():
            out += r["meta"]["notes"]
        for e in evals:
            out += e.get("assumptions", [])
        return list(dict.fromkeys(out))

    @staticmethod
    def _suggestions(c: Conditions, evals: list[dict[str, Any]]) -> list[str]:
        tips = []
        energy_fail = sorted((e for e in evals if "ENERGY" in e["failed"]), key=lambda e: e["deficit_kwh"])
        if energy_fail and c.devices:
            devices = [make_device(d.name, d.hours, d.power_w) for d in c.devices]
            s = energy.reduce_hours_suggestion(devices, energy_fail[0]["deficit_kwh"])
            if s:
                tips.append(f"{s['device']} 사용을 {s['from_hours']}시간 → {s['to_hours']}시간으로 줄이면 "
                            f"{energy_fail[0]['campsite']['name']}은(는) 조건을 맞출 수 있어요")
            tips.append("출발 전에 충전해서 잔량을 높이거나, 귀가 최소 잔량을 낮출 수 있는지 알려주세요")
        if any("POWER" in e["failed"] for e in evals):
            tips.append("출력이 큰 기기(히터 · 포트 · 인덕션)를 동시에 켜지 않도록 나눠 쓰면 출력 제한을 넘지 않아요")
        if any("RAIN" in e["failed"] for e in evals):
            tips.append("비 예보가 있는 날은 전기 사이트가 있는 캠핑장을 고르거나 날짜를 바꿔 보세요")
        if not evals:
            tips.append("다른 지역이나 더 넓은 범위를 알려주시면 다시 찾아볼게요")
        return tips

    @staticmethod
    def _draft(c: Conditions, best: dict[str, Any]) -> dict[str, Any]:
        cand = best["campsite"]
        start = datetime.combine(c.start_date, datetime.strptime(DEFAULT_DEPART, "%H:%M").time())
        end = datetime.combine(c.end_date, datetime.strptime(DEFAULT_RETURN, "%H:%M").time())
        return {
            "title": f"{cand['name']} 차박",
            "campsite": {k: cand[k] for k in ("campsite_id", "name", "address", "lat", "lon", "has_electric")},
            "origin": c.origin, "start_at": start.isoformat(timespec="minutes"), "end_at": end.isoformat(timespec="minutes"),
            "vehicle": {"capacity_kwh": c.capacity_kwh, "soc_start_pct": c.soc_start_pct,
                        "efficiency_km_per_kwh": c.efficiency_km_per_kwh or 5.0,
                        "min_return_pct": c.min_return_pct if c.min_return_pct is not None else 20},
            "devices": [d.model_dump() for d in c.devices],
            "charge_stop": best["charge_stop"] and {k: best["charge_stop"][k] for k in
                                                    ("station", "charge_kwh", "charge_minutes", "latest_arrival")},
            "expected_soc": best["metrics"]["soc"],
            "evidence": {"distance_km": best["metrics"]["distance_km_oneway"], "temp_factor": best["metrics"]["temp_factor"],
                         "rain_expected": best["metrics"].get("rain_expected"), "unjudged": best["unjudged"]},
        }

    def _write(self, kind: str, ctx: dict[str, Any], fallback: str) -> str:
        try:
            text = self.llm.write(kind, ctx)
            return text or fallback
        except Exception:  # noqa: BLE001 - 답변 문장화가 실패해도 계산 결과는 그대로 전달
            return fallback

    @staticmethod
    def _fallback_plan_text(ctx: dict[str, Any]) -> str:
        if ctx["recommendations"]:
            lines = ["조건을 맞추는 캠핑장을 찾았어요."]
            for r in ctx["recommendations"]:
                soc = r["soc"]
                line = (f"- {r['campsite']['name']}: 편도 {r['route']['distance_km']}km, "
                        f"예상 잔량 도착 {soc['arrive']:.0%} → 캠핑 후 {soc['after_camp']:.0%} → 귀가 {soc['return']:.0%}")
                if r["charge_stop"]:
                    cs = r["charge_stop"]
                    line += f" (충전 경유: {cs['station']['name']}, 약 {cs['charge_minutes']}분)"
                lines.append(line)
            return "\n".join(lines)
        return "조건을 모두 맞추는 캠핑장을 찾지 못했어요.\n" + "\n".join(f"- {s}" for s in ctx.get("suggestions") or [])

    # ------------------------------------------------------------------ 그래프

    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("analyze", self._node("analyze", self.analyze))
        g.add_node("plan", self._node("plan", self.plan))
        g.add_node("execute", self._node("execute", self.execute))
        g.add_node("judge", self._node("judge", self.judge))
        g.add_node("replan", self._node("replan", self.replan))
        g.add_node("respond", self._node("respond", self.respond))
        g.add_edge(START, "analyze")
        g.add_conditional_edges("analyze", lambda s: "respond" if s.get("status") in ("NEED_INFO", "ERROR")
                                or s.get("intent") in ("CHITCHAT", "CONFIRM_PLAN") else "plan",
                                ["plan", "respond"])
        g.add_edge("plan", "execute")
        g.add_conditional_edges("execute", lambda s: "judge" if s["intent"] == "PLAN_TRIP" else "respond",
                                ["judge", "respond"])
        g.add_conditional_edges("judge", self.route_after_judge, ["replan", "respond"])
        g.add_edge("replan", "execute")
        g.add_edge("respond", END)
        return g.compile()

    def run(self, message: str, session_id: str | None = None) -> dict[str, Any]:
        session_id = session_id or uuid.uuid4().hex
        turn_id = uuid.uuid4().hex
        em = RunEmitter(self.bus, session_id, turn_id)
        started = time.perf_counter()
        em.emit(EventType.TURN_START, message=message)
        cache: dict[str, Any] = {}
        state = self.graph.invoke({"session_id": session_id, "turn_id": turn_id, "message": message,
                                   "cache_ref": cache},
                                  {"configurable": {"emitter": em}, "recursion_limit": 60})
        self.memory.get(session_id).turns += 1
        elapsed = int((time.perf_counter() - started) * 1000)
        em.emit(EventType.TURN_END, status=state.get("status"), elapsed_ms=elapsed,
                replans=len(state.get("replans", [])))
        return {"session_id": session_id, "turn_id": turn_id, "status": state.get("status"),
                "answer": state.get("answer", ""), **(state.get("output") or {}), "elapsed_ms": elapsed,
                "timeline": [e.model_dump(mode="json") for e in em.timeline]}
