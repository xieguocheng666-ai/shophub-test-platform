"""购物车测试：加购、查看、移除、边界校验、空购物车下单。"""
from pathlib import Path

import allure
import pytest
import yaml

from api.common.assert_util import assert_status
from api.tests.constants import STORE1

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def _load_cart_cases():
    with open(ROOT_DIR / "data" / "cart.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cases = []
    for group, items in data.items():
        for item in items:
            cases.append(
                pytest.param(
                    item["store_id"], item["product_id"], item["quantity"], item["expect_code"],
                    id=item["id"],
                )
            )
    return cases


@allure.feature("购物车")
@allure.story("加购→查看→移除")
@allure.severity(allure.severity_level.CRITICAL)
def test_cart_add_get_remove(api_client, login_user):
    with allure.step("加购商品 1（数量 2）"):
        api_client.cart_add(STORE1, 1, 2)
    with allure.step("查看购物车"):
        cart = api_client.cart_get().json()
        assert len(cart["items"]) == 1
        assert cart["items"][0]["product_id"] == 1
        assert cart["items"][0]["quantity"] == 2
    with allure.step("移除商品"):
        cart = api_client.cart_remove(STORE1, 1).json()
        assert len(cart["items"]) == 0


@allure.feature("购物车")
@allure.story("加购边界校验")
@allure.severity(allure.severity_level.NORMAL)
@pytest.mark.parametrize("store_id,product_id,quantity,expect_code", _load_cart_cases())
def test_cart_add_boundary(store_id, product_id, quantity, expect_code, api_client, login_user):
    resp = api_client.cart_add(store_id, product_id, quantity)
    assert_status(resp, expect_code)


@allure.feature("购物车")
@allure.story("空购物车下单被拒")
@allure.severity(allure.severity_level.NORMAL)
def test_order_empty_cart_rejected(api_client, login_user):
    resp = api_client.order_create()
    assert_status(resp, 400)
