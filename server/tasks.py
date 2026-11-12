"""Celery 任务定义：worker 进程实际执行的业务逻辑。

任务只做「薄封装」，真正逻辑在 server/db.py（与接口层共用同一份 SQL），
避免任务与接口两套实现漂移。任务在 worker 自己的进程内连接池借还连接，
不复用 FastAPI 进程正在使用的连接；并非每个任务都新建物理连接。
"""
from server import db
from server.celery_app import celery_app


@celery_app.task(name="server.tasks.cancel_expired_orders")
def cancel_expired_orders(timeout_seconds: int):
    """扫描并取消超时未支付的订单，返回取消数量。"""
    return db.cancel_expired_orders(timeout_seconds)
