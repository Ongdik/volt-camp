"""로깅 서브 Agent.

받은 이벤트를 요약 · 가공하지 않고 발생 순서대로 그대로 저장한다.
저장에 실패해도 예외를 밖으로 던지지 않는다 (버스도 한 번 더 격리한다).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from app.db.anomalies import AnomalyRepository
from app.db.run_logs import RunLogRepository
from app.events.bus import EventBus
from app.events.types import Event, EventType
from app.logging_agent.anomaly import judge_turn

Diagnoser = Callable[[list[dict], list[dict]], str]   # (턴 이벤트, 판정 항목) → 원인 진단 (3문장 이내)

logger = logging.getLogger(__name__)


class LoggingAgent:
    """이벤트 저장(LLM 없음) + 턴이 끝나면 규칙으로 이상 징후 판정.

    이상(WARN)으로 판정된 턴만 diagnoser(LLM)로 원인을 진단하며, 메인 Agent가 기다리지 않도록 백그라운드에서 한다.
    """

    def __init__(self, repo: RunLogRepository, anomalies: AnomalyRepository | None = None,
                 diagnoser: Diagnoser | None = None, max_replan: int = 3, background: bool = True):
        self.repo = repo
        self.anomalies = anomalies
        self.diagnoser = diagnoser
        self.max_replan = max_replan
        self.background = background
        self.failed = 0
        self.diagnose_calls = 0
        self._unsubscribe: Callable[[], None] | None = None

    def handle(self, event: Event) -> None:
        try:
            self.repo.append(event)
        except Exception:  # noqa: BLE001
            self.failed += 1
            logger.exception("실행 로그 저장 실패 (session=%s, seq=%s)", event.session_id, event.seq)
            return
        if event.type is EventType.TURN_END and self.anomalies is not None:
            self._judge(event.session_id, event.turn_id)

    def _judge(self, session_id: str, turn_id: str) -> None:
        try:
            events = [e for e in self.repo.get_session(session_id) if e["turn_id"] == turn_id]
            result = judge_turn(events, self.max_replan)
            self.anomalies.save_verdict(session_id, turn_id, result["verdict"], result["findings"], result.get("reason", ""))
        except Exception:  # noqa: BLE001
            logger.exception("이상 징후 판정 실패 (turn=%s)", turn_id)
            return
        if result["verdict"] == "WARN" and self.diagnoser is not None:
            job = threading.Thread(target=self._diagnose, args=(turn_id, events, result["findings"]), daemon=True)
            job.start()
            if not self.background:
                job.join()

    def _diagnose(self, turn_id: str, events: list[dict], findings: list[dict]) -> None:
        self.diagnose_calls += 1
        try:
            text = self.diagnoser(events, findings)
            self.anomalies.save_diagnosis(turn_id, text, "DONE")
        except Exception:  # noqa: BLE001 - 진단 실패해도 판정 · 로그는 그대로
            logger.exception("이상 징후 진단 실패 (turn=%s)", turn_id)
            self.anomalies.save_diagnosis(turn_id, None, "FAILED")

    def attach(self, bus: EventBus) -> "LoggingAgent":
        self._unsubscribe = bus.subscribe(self.handle)
        return self

    def detach(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
