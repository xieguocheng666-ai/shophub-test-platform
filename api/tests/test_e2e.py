"""端到端全链路 + 异步任务测试。"""
import allure

from api.common.assert_util import assert_ok
from api.tests.constants import STORE1


@allure.feature("全链路")
@allure.story("搜索→加购→下单→支付→发货→收货")
@allure.severity(allure.severity_level.CRITICAL)
def test_full_ecommerce_flow(api_client, login_user, config):
    seller1 = config["users"]["seller1"]
    alice = config["users"]["alice"]

    with allure.step("搜索商品"):
        resp = api_client.products("罗技")
        assert_ok(resp)
        items = resp.json()["items"]
        assert len(items) >= 1
        target = items[0]

    with allure.step("把搜到的商品加入购物车"):
        api_client.cart_add(target["store_id"], target["product_id"], 1)

    with allure.step("查看购物车（确认加购成功）"):
        cart = api_client.cart_get().json()
        assert len(cart["items"]) == 1
        assert cart["items"][0]["product_id"] == target["product_id"]

    with allure.step("创建订单"):
        order_resp = api_client.order_create()
        order_id = order_resp.json()["order_id"]
        sub_order_id = order_resp.json()["sub_orders"][0]["sub_order_id"]

    with allure.step("支付"):
        assert api_client.order_pay(order_id).json()["status"] == "paid"

    with allure.step("商家发货"):
        api_client.login(seller1["username"], seller1["password"])
        assert api_client.sub_order_ship(sub_order_id).json()["status"] == "shipped"

    with allure.step("买家确认收货"):
        api_client.login(alice["username"], alice["password"])
        assert api_client.sub_order_confirm(sub_order_id).json()["status"] == "completed"


@allure.feature("异步任务")
@allure.story("超时未支付订单自动取消")
@allure.severity(allure.severity_level.CRITICAL)
def test_cancel_expired_orders(api_client, login_user):
    with allure.step("记录商品初始库存"):
        store_id, product_id = STORE1, 2
        stock_before = api_client.store_product(store_id, product_id).json()["stock"]
    with allure.step("加购并下单（pending，未支付）"):
        api_client.cart_add(store_id, product_id, 1)
        order_id = api_client.order_create().json()["order_id"]
    with allure.step("触发超时订单取消（与 Celery worker 同一份逻辑）"):
        resp = api_client.cancel_expired(0)
        assert_ok(resp)
        assert resp.json()["cancelled"] == 1
    with allure.step("断言订单已取消、库存已回补"):
        assert api_client.orders().json()["orders"][-1]["status"] == "canceled"
        assert api_client.store_product(store_id, product_id).json()["stock"] == stock_before
