"""이벤트 버스와 발행 도우미.

발행자는 누가 구독하는지 모른다. 구독자가 예외를 던져도 발행은 실패하지 않는다.
→ 로깅 Agent가 멈추거나 저장에 실패해도 메인 Agent는 정상 응답한다.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from app.events.types import Event, EventType

logger = logging.getLogger(__name__)
Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subscribers:
                    self._subscribers.remove(fn)

        return unsubscribe

    def publish(self, event: Event) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for fn in subscribers:
            try:
                fn(event)
            except Exception:  # noqa: BLE001 - 구독자 문제를 발행자에게 전파하지 않는다
                logger.exception("이벤트 구독자 오류 (event=%s)", event.type)


class RunEmitter:
    """한 턴(사용자 메시지 1건) 동안 세션 · 턴 · 순번을 붙여 발행한다. 발행한 이벤트는 timeline에도 남긴다."""

    def __init__(self, bus: EventBus | None, session_id: str, turn_id: str):
        self.bus = bus
        self.session_id = session_id
        self.turn_id = turn_id
        self.timeline: list[Event] = []
        self._seq = 0

    def emit(self, type: EventType, node: str | None = None, **payload: Any) -> Event:
        self._seq += 1
        event = Event(session_id=self.session_id, turn_id=self.turn_id, seq=self._seq,
                      type=type, node=node, payload=payload)
        self.timeline.append(event)
        if self.bus is not None:
            self.bus.publish(event)
        return event
