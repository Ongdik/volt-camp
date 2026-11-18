"""설정 상태 (키 값은 절대 돌려주지 않고 설정 여부만)."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.schemas.api import Health

router = APIRouter(prefix="/api", tags=["상태"])


@router.get("/health", response_model=Health, summary="API 키 설정 여부")
def health() -> Health:
    s = get_settings()
    return Health(llm=bool(s.openai_api_key and s.llm_model), data_go_kr=bool(s.data_go_kr_key),
                  kakao=bool(s.kakao_rest_key))
