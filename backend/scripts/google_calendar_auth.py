"""Google Calendar 연동용 OAuth 토큰을 한 번 발급한다 (CALENDAR_PROVIDER=google 일 때만 필요).

준비
1. Google Cloud 콘솔에서 Google Calendar API 사용 설정
2. OAuth 동의 화면 구성 (테스트 사용자에 본인 계정 추가)
3. 사용자 인증 정보 → OAuth 클라이언트 ID (데스크톱 앱) → JSON 다운로드
   → backend/data/google_client_secret.json 으로 저장 (GOOGLE_CLIENT_SECRET_FILE로 바꿀 수 있음)

실행 (backend/에서): python scripts/google_calendar_auth.py
브라우저에서 동의하면 GOOGLE_TOKEN_FILE(기본 data/google_token.json)이 만들어진다. 두 파일 모두 커밋하지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calendars.google import SCOPES  # noqa: E402
from app.config import get_settings  # noqa: E402


def main() -> int:
    s = get_settings()
    secret = Path(s.google_client_secret_file)
    if not secret.exists():
        print(f"OAuth 클라이언트 파일이 없습니다: {secret}")
        return 1
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES).run_local_server(port=0)
    token = Path(s.google_token_file)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    print(f"토큰 저장: {token}\n.env 에 CALENDAR_PROVIDER=google 로 바꾸고 서버를 다시 켜세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
