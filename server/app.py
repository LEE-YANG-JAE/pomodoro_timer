"""FastAPI 앱 팩토리.

★ create_app() 은 **네트워크를 쓰지 않는다.** 음원 자동 다운로드는 launcher 가
  media.enable_auto_download(True) 를 호출했을 때만 startup 훅에서 기동된다.
  덕분에 TestClient(create_app()) 가 오프라인에서 즉시 뜬다.
"""
from __future__ import annotations

import logging
import mimetypes

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .routes import media as media_routes
from .routes import playlists as playlists_routes
from .routes import search as search_routes
from .routes import settings as settings_routes
from .routes import stats as stats_routes
from .routes import tasks as tasks_routes

log = logging.getLogger(__name__)


class NoCacheStaticFiles(StaticFiles):
    """UI 자산은 매 로드마다 재검증 — 프론트 수정 후 낡은 JS 캐시를 피한다.

    ※ /media 에는 쓰지 않는다. 음원은 파일명이 불변이라 캐시되는 게 맞다.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        try:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        except Exception:
            pass
        return response


class OriginGuardMiddleware:
    """/api/* 의 상태 변경 요청에서 cross-site Origin 을 차단.

    llm_wiki 의 CORSMiddleware(allow_origins=["*"]) 를 쓰지 않는 이유:
    UI 가 같은 오리진에서 서빙되므로 CORS 로 얻는 게 없는 반면, 브라우저는
    simple request(multipart POST 등)에 preflight 를 보내지 않으므로 CORS 헤더만으로는
    쓰기를 막지 못한다. 사용자가 방문한 아무 웹사이트나
    http://127.0.0.1:8025/api/media/upload 로 POST 할 수 있게 되는 셈이다.

    순수 ASGI 로 작성한다 — BaseHTTPMiddleware 는 스트리밍 응답을 버퍼링한다.
    """

    _SAFE = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        if method in self._SAFE or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        origin = headers.get("origin")
        if origin:
            host = headers.get("host", "")
            allowed = {f"http://{host}", f"https://{host}"}
            if origin not in allowed:
                resp = JSONResponse(
                    {"detail": "다른 사이트에서 온 요청은 허용되지 않습니다."},
                    status_code=403,
                )
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """기본 보안 헤더. 순수 ASGI (llm_wiki §7.3 과 같은 이유)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"same-origin"))
                headers.append((b"x-frame-options", b"DENY"))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _register_mimetypes() -> None:
    """Windows 의 mimetypes 는 HKCR 을 읽어 설치된 프로그램에 따라 audio/mp3 를 내거나
    아무것도 못 낸다. 명시 등록해 결정론적으로 만든다."""
    mimetypes.add_type("audio/mpeg", ".mp3")
    mimetypes.add_type("audio/mp4", ".m4a")
    mimetypes.add_type("audio/flac", ".flac")
    mimetypes.add_type("audio/wav", ".wav")
    mimetypes.add_type("audio/ogg", ".ogg")
    mimetypes.add_type("audio/ogg", ".opus")


def create_app() -> FastAPI:
    _register_mimetypes()
    config.ensure_dirs()

    app = FastAPI(title="뽀모도로 타이머", version="0.1.0")
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(OriginGuardMiddleware)

    app.include_router(settings_routes.router)
    app.include_router(stats_routes.router)
    app.include_router(media_routes.router)
    app.include_router(playlists_routes.router)
    app.include_router(tasks_routes.router)
    app.include_router(search_routes.router)

    @app.on_event("startup")
    def _startup() -> None:
        # 기본 데이터 시드는 항상. 음원 자동 내려받기는 launcher 가 허용했을 때만.
        from . import media, playlists, tasks
        playlists.ensure_file()
        tasks.ensure_file()
        media.maybe_start_auto_download()

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(config.UI_DIR / "index.html")

    # 음원 — 평범한 StaticFiles. Starlette FileResponse 가 HTTP Range 를 완전 구현하므로
    # (accept-ranges / 206 단일·다중 / 416 / If-Range) <audio> 탐색이 그냥 동작한다.
    app.mount(
        "/media",
        StaticFiles(directory=str(config.MEDIA_DIR), check_dir=False),
        name="media",
    )
    app.mount(
        "/static",
        NoCacheStaticFiles(directory=str(config.UI_DIR), check_dir=False),
        name="static",
    )
    return app
