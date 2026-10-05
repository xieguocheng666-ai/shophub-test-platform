"""店铺越权矩阵 + 拆单与库存隔离测试。"""
import allure

from api.common.assert_util import assert_ok, assert_status
from api.tests.constants import STORE1, STORE2


# ---------- 越权矩阵（店铺维度） ----------
@allure.feature("越权矩阵")
@allure.story("垂直越权：买家注册店铺")
@allure.severity(allure.severity_level.NORMAL)
def test_buyer_cannot_create_store(api_client, login_user):
    resp = api_client.create_store("alice 的小店")
    assert_status(resp, 403)


@allure.feature("越权矩阵")
@allure.story("垂直越权：买家上架商品")
@allure.severity(allure.severity_level.NORMAL)
def test_buyer_cannot_add_product(api_client, login_user):
    resp = api_client.add_product(STORE1, "新商品", 99.0, 10)
    assert_status(resp, 403)


@allure.feature("越权矩阵")
@allure.story("水平越权：卖家改他人店铺库存")
@allure.severity(allure.severity_level.NORMAL)
def test_seller_cannot_operate_others_store(api_client, config):
    seller1 = config["users"]["seller1"]
    with allure.step("seller1 登录并尝试改 store2（seller2 的店）的库存"):
        api_client.login(seller1["username"], seller1["password"])
        resp = api_client.update_stock(STORE2, 1, 100)
    with allure.step("断言越权被拒（403）"):
        assert_status(resp, 403)


@allure.feature("越权矩阵")
@allure.story("水平越权：卖家发他人店铺子订单")
@allure.severity(allure.severity_level.NORMAL)
def test_seller_cannot_ship_others_sub_order(api_client, config):
    alice = config["users"]["alice"]
    seller2 = config["users"]["seller2"]
    with allure.step("alice 下单（store1 商品）并支付"):
        api_client.login(alice["username"], alice["password"])
        api_client.cart_add(STORE1, 1, 1)
        order_resp = api_client.order_create()
        order_id = order_resp.json()["order_id"]
        sub_order_id = order_resp.json()["sub_orders"][0]["sub_order_id"]
        api_client.order_pay(order_id)
    with allure.step("seller2（store2 的 owner）尝试发 store1 的子订单"):
        api_client.login(seller2["username"], seller2["password"])
        resp = api_client.sub_order_ship(sub_order_id)
    with allure.step("断言越权被拒（403）"):
        assert_status(resp, 403)


# ---------- 拆单与库存隔离 ----------
@allure.feature("拆单")
@allure.story("跨店铺下单拆成多个子订单")
@allure.severity(allure.severity_level.CRITICAL)
def test_order_split_by_store(api_client, login_user):
    with allure.step("购物车同时加购 store1 和 store2 的商品"):
        api_client.cart_add(STORE1, 1, 1)
        api_client.cart_add(STORE2, 5, 1)
    with allure.step("下单"):
        order_resp = api_client.order_create()
        assert_ok(order_resp)
        data = order_resp.json()
    with allure.step("断言拆成 2 个子订单，各属一个店铺"):
        sub_orders = data["sub_orders"]
        assert len(sub_orders) == 2
        assert {so["store_id"] for so in sub_orders} == {STORE1, STORE2}
    with allure.step("断言父订单总额 = 子订单之和"):
        assert data["total"] == sum(so["total"] for so in sub_orders)


@allure.feature("拆单")
@allure.story("同商品多店铺库存隔离")
@allure.severity(allure.severity_level.CRITICAL)
def test_same_product_multi_store_stock_isolation(api_client, login_user):
    with allure.step("记录商品 1 在两家店的库存"):
        stock1_before = api_client.store_product(STORE1, 1).json()["stock"]
        stock2_before = api_client.store_product(STORE2, 1).json()["stock"]
    with allure.step("从 store1 买 1 件商品 1"):
        api_client.cart_add(STORE1, 1, 1)
        api_client.order_create()
    with allure.step("断言 store1 库存 -1，store2 库存不变"):
        assert api_client.store_product(STORE1, 1).json()["stock"] == stock1_before - 1
        assert api_client.store_product(STORE2, 1).json()["stock"] == stock2_before


@allure.feature("拆单")
@allure.story("跨店铺支付失败，两店库存均回补")
@allure.severity(allure.severity_level.CRITICAL)
def test_cross_store_pay_fail_restores_all(api_client, login_user, payment_mode):
    payment_mode("fail")
    with allure.step("记录两店商品初始库存"):
        stock1_before = api_client.store_product(STORE1, 1).json()["stock"]
        stock5_before = api_client.store_product(STORE2, 5).json()["stock"]
    with allure.step("跨店铺加购并下单"):
        api_client.cart_add(STORE1, 1, 1)
        api_client.cart_add(STORE2, 5, 1)
        order_id = api_client.order_create().json()["order_id"]
    with allure.step("支付失败"):
        api_client.order_pay(order_id)
    with allure.step("断言两店库存均回补"):
        assert api_client.store_product(STORE1, 1).json()["stock"] == stock1_before
        assert api_client.store_product(STORE2, 5).json()["stock"] == stock5_before
