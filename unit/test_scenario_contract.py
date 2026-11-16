"""创建前的契约校验不应访问外部服务。"""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_unknown_order_state_is_rejected_before_creating_anything():
    from test_support.scenarios import TestData

    with pytest.raises(ValueError, match="state"):
        TestData("http://unused.invalid", None).order(None, [], state="invented")


def test_foreign_seller_is_rejected_before_product_creation():
    from test_support.scenarios import TestData

    with pytest.raises(ValueError, match="本例"):
        TestData("http://unused.invalid", None).product(object())


def test_interruption_and_cleanup_error_both_remain_visible(monkeypatch):
    from test_support.scenarios import TestData

    data = TestData("http://unused.invalid", None)
    def cleanup_failure():
        raise ValueError("cleanup probe")
    monkeypatch.setattr(data, "cleanup", cleanup_failure)
    with pytest.raises(BaseExceptionGroup) as captured:
        with data:
            raise KeyboardInterrupt()
    assert isinstance(captured.value.exceptions[0], KeyboardInterrupt)
    assert isinstance(captured.value.exceptions[1], ValueError)


def test_paid_preparation_rejects_http_200_business_failure():
    from test_support.scenarios import TestData
    data = TestData('http://unused.invalid', MagicMock())
    buyer = data.buyer = SimpleNamespace(params=SimpleNamespace(role='buyer'), client=MagicMock())
    seller = SimpleNamespace(params=SimpleNamespace(role='seller'))
    data._accounts = [buyer, seller]
    buyer.client.order_create.return_value = SimpleNamespace(status_code=200, json=lambda: {'order_id': 9, 'sub_orders': []})
    buyer.client.order_pay.return_value = SimpleNamespace(status_code=200, json=lambda: {'status': 'failed'})
    product = SimpleNamespace(store_id=3, product_id=4, seller=seller)
    buyer.client.cart_add.return_value = SimpleNamespace(status_code=200, json=lambda: {})
    with pytest.raises(AssertionError, match='支付'):
        data.order(buyer, [(product, 1)], state='paid')
    assert buyer.client.order_pay.call_count == 1


def test_condition_wait_has_deadline_and_never_writes():
    from test_support.scenarios import wait_until
    observe = MagicMock(return_value=False)
    with pytest.raises(AssertionError, match='就绪'):
        wait_until(observe, timeout=0.01, interval=0.001)
    assert observe.call_count > 1
