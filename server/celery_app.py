"""Celery 应用：异步任务（超时未支付自动关单）的消息队列。

架构：FastAPI（同步接口）不直接处理耗时/定时任务，交给 Celery worker 消费
Redis 队列执行；worker 进程独立启动，不阻塞请求。
"""
import os

from celery import Celery

celery_app = Celery(
    "shop",
    broker=os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1"),
    include=["server.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    result_expires=3600,              # 结果 1h 过期，避免 Redis 内存泄漏
    task_acks_late=True,              # 任务执行完才 ack，worker 崩溃不丢任务
    worker_prefetch_multiplier=1,     # 避免单 worker 囤积任务
    broker_connection_retry_on_startup=True,
)

# 周期任务：每 AUTO_CANCEL_SCAN_SECONDS 秒扫描一次，取消超过 ORDER_TIMEOUT_SECONDS 未支付的订单
celery_app.conf.beat_schedule = {
    "auto-cancel-unpaid-orders": {
        "task": "server.tasks.cancel_expired_orders",
        "schedule": float(os.getenv("AUTO_CANCEL_SCAN_SECONDS", "30")),
        "args": (int(os.getenv("ORDER_TIMEOUT_SECONDS", "1800")),),
    },
}
