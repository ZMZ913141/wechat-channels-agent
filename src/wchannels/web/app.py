"""FastAPI 实例：挂载路由 + 静态文件 + 统一错误格式。"""
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from ..config import settings
from ..db import init_db
from ..logutil import get_logger
from ..scheduler import scheduler
from .routes import (materials, accounts, publish, dashboard, media,
                     automation, cover, auth, ai, audit, works, comments,
                     messages)

log = get_logger("web")


class NoCacheStaticFiles(StaticFiles):
    """静态资源每次都向服务器校验（ETag）。

    不给 Cache-Control 时浏览器会按启发式规则缓存 JS/CSS，
    升级后用户可能一直在跑旧脚本、看到已经修好的 bug 仍在——必须禁止。
    """

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    yield
    scheduler.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="多平台视频发布工作台", version="3.3.0", lifespan=lifespan)

    # 统一错误格式：前端只认 {"detail": "..."}，任何异常都不能把英文堆栈甩给小白
    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError):
        fields = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", []) if x != "body")
            fields.append(loc or "参数")
        return JSONResponse(status_code=422,
                            content={"detail": f"提交的信息不完整或格式有误（{', '.join(fields)}）"})

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception):
        log.exception("未处理异常 %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500,
                            content={"detail": "服务器开小差了，请稍后重试；反复出现请查看数据目录 logs/app.log"})

    # API 路由
    app.include_router(materials.router, prefix="/api/materials", tags=["materials"])
    app.include_router(accounts.router, prefix="/api/accounts", tags=["accounts"])
    app.include_router(publish.router, prefix="/api/publish", tags=["publish"])
    app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
    app.include_router(media.router, prefix="/api/media", tags=["media"])
    app.include_router(automation.router, prefix="/api/automation", tags=["automation"])
    app.include_router(cover.router, prefix="/api/cover", tags=["cover"])
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(ai.router, prefix="/api/ai", tags=["ai"])
    app.include_router(audit.router, prefix="/api/audit", tags=["audit"])
    app.include_router(works.router, prefix="/api/works", tags=["works"])
    app.include_router(comments.router, prefix="/api/comments", tags=["comments"])
    app.include_router(messages.router, prefix="/api/messages", tags=["messages"])

    # 静态前端
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", NoCacheStaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html",
                            headers={"Cache-Control": "no-cache"})

    return app
