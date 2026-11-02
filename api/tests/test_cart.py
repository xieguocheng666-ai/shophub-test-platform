"""购物车测试：加购、查看、移除、边界校验、空购物车下单。"""
from pathlib import Path

import allure
import pytest
import yaml

from api.common.assert_util import assert_error
from api.common.db_util import assert_db_value
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
                    item.get("expect_detail"),
                    id=item["id"],
                )
            )
    return cases


@allure.feature("购物车")
@allure.story("加购→查看→移除")
@allure.severity(allure.severity_level.CRITICAL)
def test_cart_add_get_remove(api_client, login_user, db):
    with allure.step("加购商品 1（数量 2）"):
        api_client.cart_add(STORE1, 1, 2)
        assert_db_value(
            db,
            "SELECT quantity FROM carts WHERE username=%s AND store_id=%s AND product_id=%s",
            2, (login_user["username"], STORE1, 1), "加购应写入购物车",
        )
    with allure.step("查看购物车"):
        cart = api_client.cart_get().json()
        assert len(cart["items"]) == 1
        assert cart["items"][0]["product_id"] == 1
        assert cart["items"][0]["quantity"] == 2
    with allure.step("移除商品"):
        cart = api_client.cart_remove(STORE1, 1).json()
        assert len(cart["items"]) == 0
        assert_db_value(
            db,
            "SELECT COUNT(*) AS n FROM carts WHERE username=%s AND store_id=%s AND product_id=%s",
            0, (login_user["username"], STORE1, 1), "移除后购物车行应真删除",
        )


@allure.feature("购物车")
@allure.story("加购边界校验")
@allure.severity(allure.severity_level.NORMAL)
@pytest.mark.parametrize(
    "store_id,product_id,quantity,expect_code,expect_detail", _load_cart_cases()
)
def test_cart_add_boundary(
    store_id, product_id, quantity, expect_code, expect_detail, api_client, login_user, db
):
    resp = api_client.cart_add(store_id, product_id, quantity)
    assert_error(resp, expect_code, expect_detail)
    assert_db_value(
        db, "SELECT COUNT(*) AS n FROM carts WHERE username=%s",
        0, (login_user["username"],), "加购失败不应留下购物车数据",
    )


@allure.feature("购物车")
@allure.story("空购物车下单被拒")
@allure.severity(allure.severity_level.NORMAL)
def test_order_empty_cart_rejected(api_client, login_user, db):
    resp = api_client.order_create()
    assert_error(resp, 400, "购物车为空")
    assert_db_value(
        db, "SELECT COUNT(*) AS n FROM orders WHERE username=%s",
        0, (login_user["username"],), "空购物车下单不应生成订单",
    )
