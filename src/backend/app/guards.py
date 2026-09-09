"""请求层防护。

**深层嵌套 JSON**：`{"a":{"a":{"a": ...}}}` 嵌套两千层会让 JSON 解析器递归到栈溢出，
Python 抛 RecursionError，最终返回 500。5xx 意味着输入把代码打进了未预期路径 ——
即便这次只是解析失败，同样的手法在别的组件上就可能是拒绝服务。

防护放在解析之前：用一次线性扫描数括号深度，不做任何解析。先解析再判断深度等于
把自己要防的事先做一遍，起不到防护作用。
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

MAX_JSON_DEPTH = 64          # 业务里最深的请求体不超过 5 层, 64 已是极宽松的上限
MAX_BODY_BYTES = 2 * 1024 * 1024   # 非上传接口的正文上限; 附件走 multipart 另行限制


def _max_depth(raw: bytes, cap: int) -> int:
    """线性扫描括号深度。跳过字符串字面量, 否则名称里的括号会被误计。"""
    depth = max_depth = 0
    in_str = escape = False
    for b in raw:
        if in_str:
            if escape:
                escape = False
            elif b == 0x5C:          # \
                escape = True
            elif b == 0x22:          # "
                in_str = False
            continue
        if b == 0x22:
            in_str = True
        elif b in (0x7B, 0x5B):      # { [
            depth += 1
            if depth > max_depth:
                max_depth = depth
                if max_depth > cap:
                    return max_depth
        elif b in (0x7D, 0x5D):      # } ]
            depth -= 1
    return max_depth


class RequestGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        ctype = request.headers.get("content-type", "")
        if "application/json" in ctype:
            raw = await request.body()      # Starlette 会缓存, 下游仍可再读
            if len(raw) > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"error": {"code": "BODY_TOO_LARGE",
                                       "message": "请求正文过大", "rule": None}})
            if _max_depth(raw, MAX_JSON_DEPTH) > MAX_JSON_DEPTH:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"code": "JSON_TOO_DEEP",
                                       "message": f"请求结构嵌套过深, 上限 {MAX_JSON_DEPTH} 层",
                                       "rule": None}})
        return await call_next(request)
