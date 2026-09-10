"""UG-DCMS API 入口。"""
from __future__ import annotations

import contextlib
import asyncio

import psycopg
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from . import errors
from .api import (admin, applicability, auth, baselines, bom, dictionary, externals, families,
                  files, search, system)
from .config import get_settings
from .guards import RequestGuardMiddleware
from .db import close_pool, init_pool


class FreshStaticFiles(StaticFiles):
    """交付升级后禁止浏览器继续使用旧版离线前端资源。"""
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith((".html", ".js", ".css")) or "." not in Path(path).name:
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    from .services import backups
    backups.apply_pending_restore()
    init_pool()
    stop=asyncio.Event(); task=asyncio.create_task(backups.scheduler(stop))
    yield
    stop.set(); await task
    close_pool()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="UG-DCMS 设计构型管理系统",
        description="产品级设计构型管理 — 后端服务。"
                    "基准: UG-DCMS SRS-001 / ERD-001 / DD-001 / IVV-001 / UI-001 V1.0",
        version="1.0.0-rc2",
        lifespan=lifespan,
    )
    # 在解析之前拦掉超深/超大请求体
    app.add_middleware(RequestGuardMiddleware)

    app.add_exception_handler(errors.DcmsError, errors.dcms_error_handler)
    # 兜底: 万一仍有递归超限的路径, 也要落到 400 而不是 500
    app.add_exception_handler(RecursionError, errors.recursion_handler)
    app.add_exception_handler(psycopg.Error, errors.db_error_handler)

    app.include_router(system.router, prefix=s.api_prefix)
    app.include_router(auth.router, prefix=s.api_prefix)
    app.include_router(admin.router, prefix=s.api_prefix)
    app.include_router(dictionary.router, prefix=s.api_prefix)
    app.include_router(families.router, prefix=s.api_prefix)
    app.include_router(bom.router, prefix=s.api_prefix)
    app.include_router(applicability.router, prefix=s.api_prefix)
    app.include_router(files.router, prefix=s.api_prefix)
    app.include_router(baselines.router, prefix=s.api_prefix)
    app.include_router(search.router, prefix=s.api_prefix)
    app.include_router(externals.router, prefix=s.api_prefix)

    # Windows 原生部署可由同一 FastAPI 进程直接托管前端，不依赖 Nginx/Docker。
    # Docker 部署仍由 Nginx 托管，不受影响。
    frontend = Path(s.frontend_root) if s.frontend_root else Path(__file__).resolve().parents[2] / "frontend"
    if frontend.is_dir():
        app.mount("/", FreshStaticFiles(directory=str(frontend), html=True), name="frontend")
    return app


app = create_app()
