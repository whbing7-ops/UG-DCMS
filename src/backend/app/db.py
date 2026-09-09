"""数据库访问层。

设计选择: 直接使用 psycopg3 + SQL, 不引入 ORM 模型层。

理由是本系统的真理源是数据库本身 —— 58 张表的唯一约束、检查约束和 25 条不变量
触发器都定义在迁移脚本里(IVV AC-DATA-01 要求"数据库唯一约束实际生效, 不仅由前端
检查")。若再维护一套 ORM 模型定义, 就出现了两处 schema 定义, 二者迟早漂移, 而漂移
的后果是应用层以为自己在保护某条不变量、实际却没有。用 SQL 直接对着迁移后的库写,
schema 只有一处定义。
"""
from __future__ import annotations

import contextlib
from typing import Any, Iterator, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

_pool: ConnectionPool | None = None


def init_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        s = get_settings()
        _pool = ConnectionPool(
            s.dsn, min_size=s.pg_pool_min, max_size=s.pg_pool_max,
            kwargs={"row_factory": dict_row}, open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextlib.contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    """一个事务一个连接。异常时回滚。"""
    pool = init_pool()
    with pool.connection() as conn:
        with conn.transaction():
            yield conn


@contextlib.contextmanager
def autonomous() -> Iterator[psycopg.Connection]:
    """自治事务: 独立于调用方事务, 退出即提交。

    用途是记录"请求最终失败、但必须留痕"的事实 —— 登录失败计数、DENIED 审计。
    这类记录若写在请求事务里, 端点抛出 4xx 时会随事务一起回滚, 结果是:
    连续输错口令永远锁不上账户, 被拒绝的越权访问也不会留下任何记录。
    """
    pool = init_pool()
    with pool.connection() as conn:
        with conn.transaction():
            yield conn


def fetch_all(conn: psycopg.Connection, sql: str, params: Sequence | dict | None = None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(conn: psycopg.Connection, sql: str, params: Sequence | dict | None = None) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(conn: psycopg.Connection, sql: str, params: Sequence | dict | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def scalar(conn: psycopg.Connection, sql: str, params: Sequence | dict | None = None) -> Any:
    row = fetch_one(conn, sql, params)
    if row is None:
        return None
    return next(iter(row.values()))
