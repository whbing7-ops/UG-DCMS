"""统一错误模型。

数据库触发器抛出的不变量错误带有 DCMS-XXX 前缀, 这里把它们翻译成对使用者有意义的
HTTP 响应, 同时保留原始不变量编号, 使前端和审计都能追溯到具体是哪一条规则拦下的。
"""
from __future__ import annotations

import logging
import re

import psycopg
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

# 触发器消息形如: DCMS-INV-009: BOM 形成循环: ...
logger = logging.getLogger("dcms.errors")

# 触发器消息形如: DCMS-INV-009: BOM 形成循环: ...
_RULE_RE_UNUSED = None
_RULE_RE = re.compile(r"(DCMS-[A-Z0-9§\-]+):\s*(.*)", re.S)


class DcmsError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, rule: str | None = None):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.rule = rule


def forbidden(message: str, rule: str | None = None) -> DcmsError:
    return DcmsError(403, "FORBIDDEN", message, rule)


def not_found(message: str) -> DcmsError:
    return DcmsError(404, "NOT_FOUND", message)


def bad_request(message: str, rule: str | None = None) -> DcmsError:
    return DcmsError(400, "BAD_REQUEST", message, rule)


def unauthorized(message: str) -> DcmsError:
    return DcmsError(401, "UNAUTHORIZED", message)


def conflict(message: str, rule: str | None = None) -> DcmsError:
    return DcmsError(409, "CONFLICT", message, rule)


def license_exhausted(message: str) -> DcmsError:
    # INV-024 / SRS-ACC-003: 并发许可耗尽, 用 429 而非 403,
    # 因为这是容量限制而非权限问题, 客户端稍后重试是合理行为
    return DcmsError(429, "LICENSE_EXHAUSTED", message, "INV-024")


async def dcms_error_handler(request: Request, exc: DcmsError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "rule": exc.rule}},
    )


async def db_error_handler(request: Request, exc: psycopg.Error) -> JSONResponse:
    """把数据库不变量违规翻译成 409, 其余数据库错误按 500 处理。

    不变量违规是"用户请求违反了受控规则", 属于客户端错误; 其它数据库错误是系统问题。
    """
    raw = str(getattr(exc, "diag", None) and exc.diag.message_primary or exc)
    m = _RULE_RE.search(raw)
    if m:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "RULE_VIOLATION", "message": m.group(2).strip(),
                               "rule": m.group(1)}},
        )
    # 输入格式错误(如把 "compare" 当 uuid 传入)是客户端问题, 不是服务器故障。
    # 归到 500 会让调用方以为系统坏了, 也会淹没真正的内部错误。
    if isinstance(exc, psycopg.errors.DataError):
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_INPUT",
                               "message": "请求参数格式不正确", "rule": None}})

    constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
    if constraint:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "CONSTRAINT_VIOLATION",
                               "message": f"违反数据库约束 {constraint}",
                               "rule": constraint}},
        )
    # 生产环境不外泄数据库细节; 非生产环境保留原文, 否则排障时只能看到
    # "数据库操作失败"六个字, 真正的约束名和上下文全被吞掉。
    from .config import get_settings
    detail = raw if get_settings().environment != "PROD" else None
    logger.exception("未归类的数据库错误: %s", raw)
    return JSONResponse(status_code=500,
                        content={"error": {"code": "INTERNAL", "message": "数据库操作失败",
                                           "rule": None, "detail": detail}})


async def recursion_handler(request: Request, exc: RecursionError) -> JSONResponse:
    """递归超限属于输入过于复杂, 是客户端问题, 不该报成服务器故障。"""
    logger.warning("请求触发递归超限: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "INPUT_TOO_COMPLEX",
                           "message": "请求结构过于复杂, 无法处理", "rule": None}})
