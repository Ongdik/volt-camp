"""실행 설정. `.env`(backend/.env)와 환경변수에서 읽는다. 키 값은 로그 · 응답에 남기지 않는다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]

# .env는 모듈을 처음 불러올 때 한 번 읽는다. 시스템에 같은 이름 변수가 남아 있어도 .env가 우선한다
# (예전 키가 환경변수로 남아 있으면 401이 나는데 원인을 찾기 어렵다). 테스트는 그 뒤에 값을 덮어쓴다.
load_dotenv(BACKEND_DIR / ".env", override=True)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _path(name: str, default: Path) -> str:
    """경로 설정값. 상대 경로는 실행 위치가 아니라 backend/ 기준으로 푼다 (스크립트 · 서버가 같은 파일을 쓰도록)."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return str(default)
    p = Path(raw)
    return str(p if p.is_absolute() else (BACKEND_DIR / p).resolve())


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
    calendar_provider: str = "local"      # local | google
    google_calendar_id: str = "primary"
    google_client_secret_file: str = ""
    google_token_file: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings(
        data_go_kr_key=os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip(),
        kakao_rest_key=os.getenv("KAKAO_REST_API_KEY", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "").strip() or None,
        llm_model=os.getenv("LLM_MODEL", "").strip(),
        max_replan=_int("MAX_REPLAN", 3),
        worker_max_retry=_int("WORKER_MAX_RETRY", 1),
        tool_timeout_sec=_int("TOOL_TIMEOUT_SEC", 10),
        db_path=_path("DB_PATH", BACKEND_DIR / "data" / "app.db") if os.getenv("DB_PATH") != ":memory:" else ":memory:",
        calendar_provider=os.getenv("CALENDAR_PROVIDER", "local").strip().lower() or "local",
        google_calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "primary").strip() or "primary",
        google_client_secret_file=_path("GOOGLE_CLIENT_SECRET_FILE", BACKEND_DIR / "data" / "google_client_secret.json"),
        google_token_file=_path("GOOGLE_TOKEN_FILE", BACKEND_DIR / "data" / "google_token.json"),
    )
