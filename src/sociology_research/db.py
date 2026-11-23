"""统一的 SQLite 连接与并发配置模块。

集中配置外键约束、WAL 日志模式与忙等待超时，确保多线程读取与后台写入之间
不再因默认 rollback journal 产生锁冲突，并在退出上下文时强制关闭连接句柄。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_SQLITE_TIMEOUT_SECONDS = 15.0
DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 15_000


def open_sqlite_connection(
    database_path: Path | str,
    *,
    timeout: float = DEFAULT_SQLITE_TIMEOUT_SECONDS,
    busy_timeout_ms: int = DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """打开并配置一个统一参数的 SQLite 连接。"""
    connection = sqlite3.connect(database_path, timeout=timeout)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {max(0, int(busy_timeout_ms))}")
        # 仅对落盘数据库启用 WAL，内存库或只读文件系统上优雅忽略
        if str(database_path) != ":memory:":
            try:
                mode_row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
                if mode_row is not None and str(mode_row[0]).lower() == "wal":
                    connection.execute("PRAGMA synchronous = NORMAL")
            except sqlite3.OperationalError:
                pass
    except Exception:
        connection.close()
        raise
    return connection


@contextmanager
def sqlite_connection(
    database_path: Path | str,
    *,
    timeout: float = DEFAULT_SQLITE_TIMEOUT_SECONDS,
    busy_timeout_ms: int = DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
) -> Iterator[sqlite3.Connection]:
    """带自动提交/回滚且严格关闭连接句柄的 SQLite 上下文管理器。"""
    connection = open_sqlite_connection(
        database_path,
        timeout=timeout,
        busy_timeout_ms=busy_timeout_ms,
    )
    try:
        with connection:
            yield connection
    finally:
        connection.close()
