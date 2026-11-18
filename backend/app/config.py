"""실행 설정. `.env`(backend/.env)와 환경변수에서 읽는다. 키 값은 로그 · 응답에 남기지 않는다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_go_kr_key: str
    kakao_rest_key: str
    openai_api_key: str
    openai_base_url: str | None
    llm_model: str
    max_replan: int
    worker_max_retry: int
    tool_timeout_sec: int
    db_path: str


@lru_cache
def get_settings() -> Settings:
    load_dotenv(BACKEND_DIR / ".env")
    return Settings(
        data_go_kr_key=os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip(),
        kakao_rest_key=os.getenv("KAKAO_REST_API_KEY", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "").strip() or None,
        llm_model=os.getenv("LLM_MODEL", "").strip(),
        max_replan=_int("MAX_REPLAN", 3),
        worker_max_retry=_int("WORKER_MAX_RETRY", 1),
        tool_timeout_sec=_int("TOOL_TIMEOUT_SEC", 10),
        db_path=os.getenv("DB_PATH", str(BACKEND_DIR / "data" / "app.db")),
    )
