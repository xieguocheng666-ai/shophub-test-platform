"""创建前的契约校验不应访问外部服务。"""
import pytest


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
