"""Tool 공통 응답 형식 · 실패 코드 · 기본 클래스.

모든 Tool은 성공이든 실패든 `ToolResult` 하나로 답한다.
  - 성공: ok=True,  data=결과,  error=None
  - 실패: ok=False, data=None,  error=ToolError(code, message, retryable)
  - 공통: meta (어떤 Tool이 어떤 출처로, 무엇을 요청했고 실제로는 무엇을 조회했는지, 걸린 시간)
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


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


class ToolFailure(Exception):
    """Tool의 `run()` 안에서 '예상한 실패'를 알릴 때 던진다. `invoke()`가 실패 응답으로 바꾼다."""

    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Tool(ABC):
    """모든 Tool의 부모.

    자식은 이름 · 설명 · 입력 모델 · `run()`만 정한다.
    입력 검증, 예외 → 실패 응답 변환, 소요 시간 측정은 `invoke()`가 맡는다.
    그래서 Agent 실행 흐름은 어떤 Tool이든 예외 없이 `ToolResult`만 받는다.
    """

    name: ClassVar[str]
    description: ClassVar[str]           # Agent가 Tool을 고르는 기준. '무엇을 하는지'보다 '언제 쓰는지'를 적는다.
    input_model: ClassVar[type[BaseModel]]
    kind: ClassVar[Literal["external", "local"]] = "external"   # 외부 API 툴 / 로컬 서버 API 툴
    source: ClassVar[str] = ""

    @abstractmethod
    def run(self, args: BaseModel, meta: ToolMeta) -> dict[str, Any]:
        """실제 작업. 결과 data를 돌려준다.

        - 예상한 실패는 `ToolFailure(code, message)`를 던진다.
        - 요청과 다른 기준으로 조회했거나 추정이 섞였으면 `meta.actual` · `meta.estimated` · `meta.notes`를 채운다.
        """

    def invoke(self, raw_args: dict[str, Any] | None = None) -> ToolResult:
        raw_args = raw_args or {}
        meta = ToolMeta(tool=self.name, source=self.source, requested=dict(raw_args))
        started = time.perf_counter()
        try:
            args = self.input_model.model_validate(raw_args)
            data = self.run(args, meta)
            result = ToolResult.success(data, meta)
        except ValidationError as exc:
            fields = ", ".join(".".join(str(p) for p in err["loc"]) or "(입력)" for err in exc.errors())
            result = ToolResult.failure(ErrorCode.INVALID_INPUT, f"입력값을 확인해 주세요: {fields}", meta)
        except ToolFailure as exc:
            result = ToolResult.failure(exc.code, exc.message, meta)
        except Exception:  # noqa: BLE001 - Tool 버그가 서비스 전체를 멈추지 않게 한다
            logger.exception("Tool %s 실행 중 예상 못 한 오류", self.name)
            result = ToolResult.failure(ErrorCode.INTERNAL_ERROR, "도구 실행 중 오류가 발생했습니다", meta)
        result.meta.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return result

    @classmethod
    def spec(cls) -> dict[str, Any]:
        """LLM에 넘길 명세 (OpenAI tool calling 형식)."""
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": cls.input_model.model_json_schema(),
            },
        }
