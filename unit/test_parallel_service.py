"""进程隔离所需的服务契约；不连接真实数据库。"""
from unittest.mock import MagicMock
import asyncio

import pytest

from server import db, main


def test_pool_budget_is_configurable(monkeypatch):
    monkeypatch.setenv('DB_POOL_MIN', '0')
    monkeypatch.setenv('DB_POOL_MAX', '10')
    monkeypatch.setattr(db, '_POOL', None)
    constructor = MagicMock()
    monkeypatch.setattr(db, 'PooledDB', constructor)
    db._get_pool()
    assert constructor.call_args.kwargs['mincached'] == 0
    assert constructor.call_args.kwargs['maxconnections'] == 10
    assert constructor.call_args.kwargs['maxcached'] == 10


def test_health_confirms_environment_and_database(monkeypatch):
    monkeypatch.setenv('TEST_ENVIRONMENT_ID', 'env-example')
    monkeypatch.setenv('DB_NAME', 'owned-example')
    assert main.health()['environment_id'] == 'env-example'
    assert main.health()['database'] == 'owned-example'


@pytest.mark.parametrize('mode', ['success', 'fail', 'timeout'])
def test_payment_in_flight_covers_processing_and_always_finishes(monkeypatch, mode):
    monkeypatch.setattr(main, 'PAYMENT_MODE', mode)
    monkeypatch.setattr(db, 'get_order', lambda *a: {'status': 'pending'})
    observed = []
    def observe(*args):
        observed.append(main.payment_state()['in_flight'])
    monkeypatch.setattr(db, 'pay_order', observe)
    monkeypatch.setattr(db, 'cancel_order', observe)
    monkeypatch.setattr(main.time, 'sleep', observe)
    async def endpoint(scope, receive, send):
        assert main.payment_state()['in_flight'] == 1
        main.pay_order(1, 'buyer')
    asyncio.run(main.PaymentLifecycleMiddleware(endpoint)(
        {'type': 'http', 'method': 'POST', 'path': '/order/1/pay'}, None, None))
    assert observed == [1]
    assert main.payment_state()['in_flight'] == 0


def test_payment_exception_decrements_counter(monkeypatch):
    monkeypatch.setattr(main, 'PAYMENT_MODE', 'success')
    monkeypatch.setattr(db, 'get_order', lambda *a: {'status': 'pending'})
    monkeypatch.setattr(db, 'pay_order', MagicMock(side_effect=RuntimeError('probe')))
    async def endpoint(scope, receive, send):
        main.pay_order(1, 'buyer')
    with pytest.raises(RuntimeError):
        asyncio.run(main.PaymentLifecycleMiddleware(endpoint)(
            {'type': 'http', 'method': 'POST', 'path': '/order/1/pay'}, None, None))
    assert main.payment_state()['in_flight'] == 0


def test_payment_queued_before_handler_starts_is_already_in_flight():
    async def probe():
        entered, release = asyncio.Event(), asyncio.Event()
        async def queued_endpoint(scope, receive, send):
            entered.set()
            await release.wait()  # 模拟同步handler尚未取得线程池执行机会。
        pending = asyncio.create_task(main.PaymentLifecycleMiddleware(queued_endpoint)(
            {'type': 'http', 'method': 'POST', 'path': '/order/9/pay'}, None, None))
        await entered.wait()
        try:
            assert main.payment_state()['in_flight'] == 1
        finally:
            release.set()
            await pending
        assert main.payment_state()['in_flight'] == 0
    asyncio.run(probe())


def test_other_routes_do_not_count_as_payment():
    async def endpoint(scope, receive, send):
        assert main.payment_state()['in_flight'] == 0
    asyncio.run(main.PaymentLifecycleMiddleware(endpoint)(
        {'type': 'http', 'method': 'GET', 'path': '/debug/payment-state'}, None, None))
