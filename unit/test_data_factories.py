"""数据工厂生成的是测试参数，不访问数据库或被测服务。"""
from decimal import Decimal

import pytest


def test_user_defaults_and_overrides_are_isolated():
    from test_support.factories import DataFactory

    factory = DataFactory(namespace="sample")
    buyer = factory.user()
    seller = factory.user(role="seller", nickname="商家甲")
    assert buyer.role == "buyer"
    assert seller.role == "seller" and seller.nickname == "商家甲"
    assert buyer.username != seller.username
    assert buyer.username.startswith("sample_")
    assert buyer.password


def test_default_factories_do_not_reuse_names():
    from test_support.factories import DataFactory

    assert DataFactory().user().username != DataFactory().user().username


def test_product_has_exact_explicit_price_and_independent_names():
    from test_support.factories import DataFactory

    factory = DataFactory()
    first = factory.product(price="19.90", stock=0)
    second = factory.product(price=Decimal("0.01"), stock=3, name="边界商品")
    assert first.price == Decimal("19.90") and first.stock == 0
    assert second.price == Decimal("0.01") and second.name == "边界商品"
    assert factory.product().name != factory.product().name


@pytest.mark.parametrize("role", ["admin", "", None])
def test_user_factory_rejects_unknown_roles(role):
    from test_support.factories import DataFactory

    with pytest.raises(ValueError, match="role"):
        DataFactory().user(role=role)


@pytest.mark.parametrize("price,stock", [("NaN", 1), ("-1", 1), ("1.001", 1), ("1", -1)])
def test_default_valid_product_factory_rejects_inconsistent_inputs(price, stock):
    from test_support.factories import DataFactory

    with pytest.raises(ValueError):
        DataFactory().product(price=price, stock=stock)
