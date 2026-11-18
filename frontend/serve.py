"""프론트엔드 정적 서버 + 백엔드 API 프록시.

- `python -m http.server`는 확장자별 MIME을 OS 설정에서 가져와, 일부 Windows에서 .js를 text/plain으로 보낸다.
  브라우저는 그런 ES 모듈을 실행하지 않으므로 MIME을 직접 고정한다.
- 화면과 같은 출처(origin)로 /api/* 요청을 백엔드에 넘겨, 브라우저의 교차 출처 제한(CORS) 없이 동작하게 한다.

사용: python serve.py [포트] [백엔드 주소]   (기본 5173, http://127.0.0.1:8000)
"""

import http.server
import sys
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path

MIME = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html",
        ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png"}
BACKEND = "http://127.0.0.1:8000"


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, **MIME}

    def end_headers(self):
        # 개발 서버: 예전에 잘못된 MIME으로 받은 파일이 캐시에 남지 않게 매번 새로 받는다
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _proxy(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(BACKEND + self.path, data=body, method=self.command,
                                     headers={"Content-Type": self.headers.get("Content-Type", "application/json")})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                status, data, ctype = resp.status, resp.read(), resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            status, data, ctype = e.code, e.read(), e.headers.get("Content-Type", "application/json")
        except OSError:
            status, ctype = 502, "application/json"
            data = '{"error":{"code":"BACKEND_DOWN","message":"백엔드 서버에 연결하지 못했어요. 서버가 켜져 있는지 확인해 주세요."}}'.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        return super().do_GET()

    def do_POST(self):
        return self._proxy()

    def do_PUT(self):
        return self._proxy()

    def do_DELETE(self):
        return self._proxy()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
    BACKEND = sys.argv[2] if len(sys.argv) > 2 else BACKEND
    handler = partial(Handler, directory=str(Path(__file__).resolve().parent))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"프론트엔드: http://127.0.0.1:{port}  (API → {BACKEND})")
        httpd.serve_forever()
