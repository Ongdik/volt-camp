"""Google Calendar API 구현체 (`CALENDAR_PROVIDER=google`).

인증은 OAuth 사용자 동의 방식이다. `scripts/google_calendar_auth.py`를 한 번 실행해
`GOOGLE_TOKEN_FILE`(기본 data/google_token.json)을 만든 뒤 쓴다. 키 파일 · 토큰 파일은 저장소에 올리지 않는다.

VoltCamp가 등록한 일정은 비공개 확장 속성(voltcamp_plan_id)에 계획 번호를 남겨,
같은 계획을 다시 등록하면 새로 만들지 않고 기존 일정을 돌려준다.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from app.calendars.base import CalendarEvent, to_aware, to_local
from app.clients.kma import KST
from app.tools.base import ErrorCode, ToolFailure

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
PLAN_KEY = "voltcamp_plan_id"
SOURCE = "Google Calendar API"


def _when(raw: dict[str, Any], *, end: bool) -> tuple[str, bool]:
    """Google 시각 → (KST 문자열, 종일 여부). 종일 일정의 end.date는 다음 날 0시(포함하지 않음)다."""
    if "dateTime" in raw:
        return to_local(datetime.fromisoformat(raw["dateTime"].replace("Z", "+00:00"))), False
    day = date.fromisoformat(raw["date"])
    return to_local(datetime.combine(day, time.min, KST)), True


def _event(item: dict[str, Any]) -> CalendarEvent:
    start, all_day = _when(item["start"], end=False)
    end, _ = _when(item["end"], end=True)
    plan = ((item.get("extendedProperties") or {}).get("private") or {}).get(PLAN_KEY)
    return CalendarEvent(event_id=item["id"], title=item.get("summary") or "(제목 없음)", start_at=start, end_at=end,
                         plan_id=int(plan) if plan and str(plan).isdigit() else None, all_day=all_day)


def _failure(exc: Exception) -> ToolFailure:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status in (401, 403):
        return ToolFailure(ErrorCode.AUTH_FAILED, "Google Calendar 인증에 실패했습니다. 토큰을 다시 발급해 주세요")
    if status == 429:
        return ToolFailure(ErrorCode.QUOTA_EXCEEDED, "Google Calendar 호출 한도를 넘었습니다")
    if status == 404:
        return ToolFailure(ErrorCode.INVALID_INPUT, "GOOGLE_CALENDAR_ID에 해당하는 캘린더를 찾을 수 없습니다")
    return ToolFailure(ErrorCode.UPSTREAM_ERROR, "Google Calendar 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요")


def build_service(token_file: str):
    """저장된 OAuth 토큰으로 Calendar API 클라이언트를 만든다. 토큰이 없으면 MISSING_API_KEY."""
    from pathlib import Path

    if not token_file or not Path(token_file).exists():
        raise ToolFailure(ErrorCode.MISSING_API_KEY,
                          "Google Calendar 토큰이 없습니다. scripts/google_calendar_auth.py로 먼저 인증해 주세요")
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        Path(token_file).write_text(creds.to_json(), encoding="utf-8")
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


class GoogleCalendarProvider:
    name = "google"

    def __init__(self, calendar_id: str = "primary", token_file: str = "", service: Any = None):
        self.calendar_id = calendar_id or "primary"
        self.token_file = token_file
        self._service = service   # 테스트에서는 가짜 서비스를 넣는다

    @property
    def service(self):
        if self._service is None:
            self._service = build_service(self.token_file)
        return self._service

    def _call(self, request) -> dict[str, Any]:
        try:
            return request.execute()
        except ToolFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - HttpError · 네트워크 오류를 ToolFailure로
            raise _failure(exc) from exc

    def list_events(self, start_at: str, end_at: str) -> list[CalendarEvent]:
        data = self._call(self.service.events().list(
            calendarId=self.calendar_id, timeMin=to_aware(start_at).isoformat(), timeMax=to_aware(end_at).isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=50))
        return [_event(i) for i in data.get("items", []) if i.get("status") != "cancelled"]

    def find_by_plan(self, plan_id: int) -> CalendarEvent | None:
        data = self._call(self.service.events().list(
            calendarId=self.calendar_id, privateExtendedProperty=f"{PLAN_KEY}={plan_id}", maxResults=1))
        items = [i for i in data.get("items", []) if i.get("status") != "cancelled"]
        return _event(items[0]) if items else None

    def create_event(self, title: str, start_at: str, end_at: str, plan_id: int, description: str) -> CalendarEvent:
        body = {"summary": title, "description": description,
                "start": {"dateTime": to_aware(start_at).isoformat(), "timeZone": "Asia/Seoul"},
                "end": {"dateTime": to_aware(end_at).isoformat(), "timeZone": "Asia/Seoul"},
                "extendedProperties": {"private": {PLAN_KEY: str(plan_id)}}}
        return _event(self._call(self.service.events().insert(calendarId=self.calendar_id, body=body)))
