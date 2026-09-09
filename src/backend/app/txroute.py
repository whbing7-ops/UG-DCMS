"""按请求持有事务的路由类。

为什么不用 `yield` 依赖管理事务: FastAPI 自 0.106 起, 带 yield 的依赖其清理代码
在**响应发出之后**才执行。对数据库事务而言这意味着提交晚于响应返回 —— 客户端
拿到 201 并立刻使用刚建的对象时, 可能读不到它。这类失败是间歇性的, 负载越轻越
不容易复现, 却会在真实使用中反复出现, 最伤使用者对系统的信任。

这里把事务放在路由处理器内部: `with transaction()` 在 `custom()` 返回前退出,
提交因此必然早于响应交回 Starlette。端点抛出异常时事务回滚, 与
`db.autonomous()` 配合 —— 需要在失败请求中留痕的记录(登录失败计数、DENIED 审计)
走自治事务, 不受本次回滚影响。
"""
from __future__ import annotations

from typing import Callable

from fastapi import Request, Response
from fastapi.routing import APIRoute

from .db import transaction


class TransactionalRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def custom(request: Request) -> Response:
            with transaction() as conn:
                request.state.conn = conn
                return await original(request)

        return custom
