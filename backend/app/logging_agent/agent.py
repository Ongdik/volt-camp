"""로깅 서브 Agent.

받은 이벤트를 요약 · 가공하지 않고 발생 순서대로 그대로 저장한다.
저장에 실패해도 예외를 밖으로 던지지 않는다 (버스도 한 번 더 격리한다).
"""

from __future__ import annotations

import logging
from typing import Callable

from app.db.run_logs import RunLogRepository
from app.events.bus import EventBus
from app.events.types import Event

logger = logging.getLogger(__name__)


class LoggingAgent:
    def __init__(self, repo: RunLogRepository):
        self.repo = repo
        self.failed = 0
        self._unsubscribe: Callable[[], None] | None = None

    def handle(self, event: Event) -> None:
        try:
            self.repo.append(event)
        except Exception:  # noqa: BLE001
            self.failed += 1
            logger.exception("실행 로그 저장 실패 (session=%s, seq=%s)", event.session_id, event.seq)

    def attach(self, bus: EventBus) -> "LoggingAgent":
        self._unsubscribe = bus.subscribe(self.handle)
        return self

    def detach(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
