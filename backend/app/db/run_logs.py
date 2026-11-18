"""실행 로그 저장소: 추가 · 목록 · 상세 · 삭제. 기록을 고칠 수 있으면 관찰의 의미가 없어서 수정은 두지 않는다."""

from __future__ import annotations

import json
from typing import Any

from app.db.database import Database
from app.events.types import Event


class RunLogRepository:
    def __init__(self, db: Database):
        self.db = db

    def append(self, event: Event) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO run_events (event_id, session_id, turn_id, seq, ts, type, node, payload_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event.event_id, event.session_id, event.turn_id, event.seq, event.ts,
                 event.type.value, event.node, json.dumps(event.payload, ensure_ascii=False, default=str)),
            )

    def list_sessions(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("""
                SELECT session_id,
                       MIN(ts) AS started_at, MAX(ts) AS last_at,
                       COUNT(DISTINCT turn_id) AS turns, COUNT(*) AS events,
                       SUM(type = 'REPLAN') AS replans, SUM(type = 'ERROR') AS errors,
                       SUM(type = 'TOOL_CALL') AS tool_calls
                FROM run_events GROUP BY session_id ORDER BY MAX(id) DESC
            """).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT event_id, session_id, turn_id, seq, ts, type, node, payload_json"
                " FROM run_events WHERE session_id = ? ORDER BY id", (session_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json"))
            out.append(d)
        return out

    def delete_session(self, session_id: str) -> int:
        with self.db.tx() as c:
            return c.execute("DELETE FROM run_events WHERE session_id = ?", (session_id,)).rowcount
