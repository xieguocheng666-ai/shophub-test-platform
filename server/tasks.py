"""Celery 任务定义：worker 进程实际执行的业务逻辑。

任务只做「薄封装」，真正逻辑在 server/db.py（与接口层共用同一份 SQL），
避免任务与接口两套实现漂移。数据库连接在任务内部新建（worker 是独立进程，
不能复用 FastAPI 进程里的连接）。
"""
from server import db
from server.celery_app import celery_app


@celery_app.task(name="server.tasks.cancel_expired_orders")
def cancel_expired_orders(timeout_seconds: int):
    """扫描并取消超时未支付的订单，返回取消数量。"""
    return db.cancel_expired_orders(timeout_seconds)
