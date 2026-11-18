"""실행 로그 API: 목록 · 상세 · 삭제만 (수정 없음)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.api import deps
from app.api.errors import ApiError
from app.db.run_logs import RunLogRepository

router = APIRouter(prefix="/api/logs", tags=["실행 로그"])


@router.get("", summary="세션별 실행 로그 목록")
def list_logs(logs: RunLogRepository = Depends(deps.run_logs)) -> list[dict]:
    return logs.list_sessions()


@router.get("/{session_id}", summary="세션 실행 로그 상세 (발생 순서)")
def get_log(session_id: str, logs: RunLogRepository = Depends(deps.run_logs)) -> list[dict]:
    events = logs.get_session(session_id)
    if not events:
        raise ApiError(404, "LOG_NOT_FOUND", "해당 세션의 실행 로그가 없습니다")
    return events


@router.delete("/{session_id}", status_code=204, summary="세션 실행 로그 삭제")
def delete_log(session_id: str, logs: RunLogRepository = Depends(deps.run_logs)) -> Response:
    if logs.delete_session(session_id) == 0:
        raise ApiError(404, "LOG_NOT_FOUND", "해당 세션의 실행 로그가 없습니다")
    return Response(status_code=204)
