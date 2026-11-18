"""Tool 공통 응답 형식과 실패 코드.

모든 Tool은 성공이든 실패든 `ToolResult` 하나로 답한다.
  - 성공: ok=True,  data=결과,  error=None
  - 실패: ok=False, data=None,  error=ToolError(code, message, retryable)
  - 공통: meta (어떤 Tool이 어떤 출처로, 무엇을 요청했고 실제로는 무엇을 조회했는지, 걸린 시간)
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    """Tool 실패 코드. 실행 흐름은 이 코드로 재시도 · 재계획 · 안내를 결정한다."""

    MISSING_API_KEY = "MISSING_API_KEY"   # 환경변수에 키 없음
    AUTH_FAILED = "AUTH_FAILED"           # 키 오류 · 미승인
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"     # 호출 한도 초과
    TIMEOUT = "TIMEOUT"                   # 응답 시간 초과
    UPSTREAM_ERROR = "UPSTREAM_ERROR"     # 외부 서버 오류 · 연결 실패
    INVALID_INPUT = "INVALID_INPUT"       # 입력 누락 · 형식 오류
    NO_RESULT = "NO_RESULT"               # 결과 0건
    NO_ROUTE = "NO_ROUTE"                 # 경로 없음
    OUT_OF_RANGE = "OUT_OF_RANGE"         # 제공 범위 밖 (예: 예보 기간 밖 날짜)
    PARSE_ERROR = "PARSE_ERROR"           # 응답 형식이 예상과 다름
    INTERNAL_ERROR = "INTERNAL_ERROR"     # Tool 코드의 예상 못 한 오류 (버그)


# 같은 호출을 한 번 더 해 볼 만한 실패 (일시적인 문제)
RETRYABLE_CODES: frozenset[ErrorCode] = frozenset({ErrorCode.TIMEOUT, ErrorCode.UPSTREAM_ERROR})

# 다시 계획해도 결과가 같은 실패 → 재계획하지 않고 바로 사용자에게 안내
FATAL_CODES: frozenset[ErrorCode] = frozenset(
    {ErrorCode.MISSING_API_KEY, ErrorCode.AUTH_FAILED, ErrorCode.QUOTA_EXCEEDED}
)


class ToolError(BaseModel):
    code: ErrorCode
    message: str                 # 사용자에게 보여도 되는 문장 (키 · 스택 · 원본 URL 금지)
    retryable: bool = False

    @property
    def is_fatal(self) -> bool:
        return self.code in FATAL_CODES


class ToolMeta(BaseModel):
    tool: str
    source: str = ""                                          # 데이터 출처 (예: "Open-Meteo")
    requested: dict[str, Any] = Field(default_factory=dict)   # 요청받은 기준
    actual: dict[str, Any] = Field(default_factory=dict)      # 실제로 조회한 기준 (다르면 응답에 밝힌다)
    estimated: bool = False                                    # 추정이 섞였는지
    notes: list[str] = Field(default_factory=list)            # 사용자에게 밝힐 사항
    elapsed_ms: int = 0


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None
    meta: ToolMeta

    @classmethod
    def success(cls, data: dict[str, Any], meta: ToolMeta) -> ToolResult:
        return cls(ok=True, data=data, meta=meta)

    @classmethod
    def failure(cls, code: ErrorCode, message: str, meta: ToolMeta) -> ToolResult:
        error = ToolError(code=code, message=message, retryable=code in RETRYABLE_CODES)
        return cls(ok=False, error=error, meta=meta)
