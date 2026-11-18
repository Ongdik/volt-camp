"""프론트엔드 정적 서버.

`python -m http.server`는 확장자별 MIME을 OS 설정에서 가져와, 일부 Windows에서 .js를 text/plain으로 보낸다.
브라우저는 그런 ES 모듈을 실행하지 않으므로 MIME을 직접 고정한다.

사용: python serve.py [포트]   (기본 5173)
"""

import http.server
import sys
from functools import partial
from pathlib import Path

MIME = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html",
        ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png"}


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, **MIME}


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
    handler = partial(Handler, directory=str(Path(__file__).resolve().parent))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"프론트엔드: http://127.0.0.1:{port}")
        httpd.serve_forever()
