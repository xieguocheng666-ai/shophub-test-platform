"""购物车测试：加购、查看、移除、边界校验、空购物车下单。"""
import allure

from api.common.assert_util import assert_status

# 种子数据里的店铺 id（server/db.py SEED_STORES）
STORE1, STORE2 = 1, 2


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
@allure.story("库存不足被拒")
@allure.severity(allure.severity_level.NORMAL)
def test_cart_add_insufficient_stock(api_client, login_user):
    # 商品 4 库存 30，999 远超库存，必触发 400
    resp = api_client.cart_add(STORE1, 4, 999)
    assert_status(resp, 400)


@allure.feature("购物车")
@allure.story("数量非法被拒")
@allure.severity(allure.severity_level.NORMAL)
def test_cart_add_invalid_quantity(api_client, login_user):
    resp = api_client.cart_add(STORE1, 1, 0)
    assert_status(resp, 400)


@allure.feature("购物车")
@allure.story("商品不存在")
@allure.severity(allure.severity_level.NORMAL)
def test_cart_add_nonexistent_product(api_client, login_user):
    resp = api_client.cart_add(STORE1, 9999, 1)
    assert_status(resp, 404)


@allure.feature("购物车")
@allure.story("空购物车下单被拒")
@allure.severity(allure.severity_level.NORMAL)
def test_order_empty_cart_rejected(api_client, login_user):
    resp = api_client.order_create()
    assert_status(resp, 400)
