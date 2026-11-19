"""캘린더 구현체 선택. CALENDAR_PROVIDER=local(기본) | google."""

from __future__ import annotations

from app.calendars.base import CalendarEvent, CalendarProvider
from app.calendars.google import GoogleCalendarProvider
from app.calendars.local import LocalCalendarProvider
from app.config import get_settings
from app.db.database import Database

__all__ = ["CalendarEvent", "CalendarProvider", "make_calendar", "LocalCalendarProvider", "GoogleCalendarProvider"]


def make_calendar(db: Database) -> CalendarProvider:
    s = get_settings()
    if s.calendar_provider == "google":
        return GoogleCalendarProvider(s.google_calendar_id, s.google_token_file)
    return LocalCalendarProvider(db)
