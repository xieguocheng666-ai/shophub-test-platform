"""独立数据库校验：直连 MySQL，不复用 server/db.py 的连接和 SQL。

为什么独立：接口读、接口写都走被测代码；如果读写逻辑以同一种方式出错，
API 往返会“自洽地”通过。直连数据库才能作为独立信源，证明数据真的落库了。
"""
import os

import pymysql
from pymysql.cursors import DictCursor


def connect(**overrides):
    """按 .env 的 DB_* 建立连接（测试进程与被测进程共用同一个库）。"""
    kwargs = {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "shop"),
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": True,  # 只读查询，不留长事务，每条 SELECT 都读最新已提交数据
    }
    kwargs.update(overrides)
    return pymysql.connect(**kwargs)


class DB:
    """薄封装：one / all / scalar，够写断言即可。"""

    def __init__(self, conn):
        self.conn = conn

    def one(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def all(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def scalar(self, sql, params=None):
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None


def assert_db_value(db, sql, expected, params=None, note=""):
    """断言单值查询结果；失败信息带 SQL 和参数，能直接定位是哪条数据不对。"""
    actual = db.scalar(sql, params)
    prefix = f"{note}：" if note else ""
    assert actual == expected, (
        f"{prefix}数据库校验失败，期望 {expected}，实际 {actual}\n"
        f"SQL: {sql}\n参数: {params}"
    )
