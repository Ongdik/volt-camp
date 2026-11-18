"""오류 응답: {error: {code, message}}. 스택 · 키 · 외부 API 원본 메시지는 응답에 넣지 않는다."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content=body(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError):
        fields = ", ".join(".".join(str(p) for p in e["loc"][1:]) for e in exc.errors())
        return JSONResponse(status_code=422, content=body("INVALID_INPUT", f"입력값을 확인해 주세요: {fields}"))

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError):
        return JSONResponse(status_code=400, content=body("INVALID_INPUT", str(exc)))

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception):
        logger.exception("처리하지 못한 오류")
        return JSONResponse(status_code=500, content=body("INTERNAL_ERROR", "서버 오류가 발생했습니다"))
