"""로컬 저장소(SQLite) 캘린더 구현체. Google 연동 전에 Tool 동작을 확인하는 용도이자 기본값."""

from __future__ import annotations

from datetime import datetime, timezone

from app.calendars.base import CalendarEvent
from app.db.database import Database


class LocalCalendarProvider:
    name = "local"

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _event(row) -> CalendarEvent:
        return CalendarEvent(event_id=f"local-{row['id']}", title=row["title"], start_at=row["start_at"],
                             end_at=row["end_at"], plan_id=row["plan_id"])

    def list_events(self, start_at: str, end_at: str) -> list[CalendarEvent]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM calendar_events WHERE start_at < ? AND end_at > ? ORDER BY start_at, id",
                             (end_at, start_at)).fetchall()
        return [self._event(r) for r in rows]

    def find_by_plan(self, plan_id: int) -> CalendarEvent | None:
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM calendar_events WHERE plan_id = ?", (plan_id,)).fetchone()
        return self._event(row) if row else None

    def create_event(self, title: str, start_at: str, end_at: str, plan_id: int, description: str) -> CalendarEvent:
        with self.db.tx() as c:
            cur = c.execute("INSERT INTO calendar_events (title, start_at, end_at, plan_id, description, created_at)"
                            " VALUES (?, ?, ?, ?, ?, ?)",
                            (title, start_at, end_at, plan_id, description,
                             datetime.now(timezone.utc).isoformat(timespec="seconds")))
            row = c.execute("SELECT * FROM calendar_events WHERE id = ?", (cur.lastrowid,)).fetchone()
        return self._event(row)
