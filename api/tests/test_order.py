"""订单状态机 + 支付异常测试：全流程、非法流转、越权、支付失败/超时。"""
import allure
import pytest
import requests

from api.api_client import ApiClient
from api.common.assert_util import assert_ok, assert_status
from api.tests.constants import STORE1, STORE2


# ---------- 状态机 ----------
@allure.feature("状态机")
@allure.story("全流程")
@allure.severity(allure.severity_level.CRITICAL)
def test_order_full_flow(api_client, login_user, config):
    seller1 = config["users"]["seller1"]
    alice = config["users"]["alice"]

    with allure.step("加购商品到购物车"):
        api_client.cart_add(STORE1, 2, 1)

    with allure.step("创建订单"):
        order_resp = api_client.order_create()
        assert_ok(order_resp)
        order_id = order_resp.json()["order_id"]
        sub_order_id = order_resp.json()["sub_orders"][0]["sub_order_id"]
        assert order_resp.json()["status"] == "pending"

    with allure.step("支付订单"):
        pay_resp = api_client.order_pay(order_id)
        assert pay_resp.json()["status"] == "paid"
        allure.attach(
            str(pay_resp.status_code),
            name="状态码",
            attachment_type=allure.attachment_type.TEXT,
        )
        allure.attach(
            pay_resp.text,
            name="响应体",
            attachment_type=allure.attachment_type.JSON,
        )

    with allure.step("商家发货"):
        api_client.login(seller1["username"], seller1["password"])
        assert api_client.sub_order_ship(sub_order_id).json()["status"] == "shipped"
    with allure.step("买家确认收货"):
        api_client.login(alice["username"], alice["password"])
        assert api_client.sub_order_confirm(sub_order_id).json()["status"] == "completed"


@allure.feature("状态机")
@allure.story("非法状态流转")
@allure.severity(allure.severity_level.NORMAL)
def test_illegal_transition_rejected(api_client, login_user):
    with allure.step("加购商品"):
        api_client.cart_add(STORE1, 3, 1)
    with allure.step("创建订单"):
        sub_order_id = api_client.order_create().json()["sub_orders"][0]["sub_order_id"]
    with allure.step("待支付直接确认收货（预期 400）"):
        resp = api_client.sub_order_confirm(sub_order_id)
        assert_status(resp, 400)


@allure.feature("状态机")
@allure.story("水平越权")
@allure.severity(allure.severity_level.NORMAL)
def test_cannot_operate_others_order(api_client, config):
    alice = config["users"]["alice"]
    with allure.step("alice 登录并创建订单"):
        api_client.login(alice["username"], alice["password"])
        api_client.cart_add(STORE1, 2, 1)
        order_id = api_client.order_create().json()["order_id"]

    bob = config["users"]["bob"]
    with allure.step("bob 登录并尝试支付 alice 的订单"):
        api_client.login(bob["username"], bob["password"])
        resp = api_client.order_pay(order_id)
    with allure.step("断言越权被拒（404）"):
        assert_status(resp, 404)


@allure.feature("状态机")
@allure.story("垂直越权")
@allure.severity(allure.severity_level.NORMAL)
def test_buyer_cannot_ship(api_client, config):
    alice = config["users"]["alice"]
    with allure.step("alice 登录并创建订单"):
        api_client.login(alice["username"], alice["password"])
        api_client.cart_add(STORE1, 2, 1)
        sub_order_id = api_client.order_create().json()["sub_orders"][0]["sub_order_id"]

    with allure.step("买家尝试发货（无 seller 角色，预期 403）"):
        resp = api_client.sub_order_ship(sub_order_id)
    with allure.step("断言垂直越权被拒（403）"):
        assert_status(resp, 403)


# ---------- 支付异常 ----------
@allure.feature("支付异常")
@allure.story("订单取消，仓库回补")
@allure.severity(allure.severity_level.CRITICAL)
def test_pay_fail_cancels_and_restores_stock(api_client, login_user, payment_mode):
    payment_mode("fail")
    store_id, product_id = STORE2, 5
    stock_before = api_client.store_product(store_id, product_id).json()["stock"]
    with allure.step("加购"):
        api_client.cart_add(store_id, product_id, 2)

    with allure.step("下单"):
        order_id = api_client.order_create().json()["order_id"]

    with allure.step("支付失败"):
        resp = api_client.order_pay(order_id)
        assert_ok(resp)
        assert resp.json()["status"] == "failed"
    with allure.step("订单取消"):
        assert api_client.orders().json()["orders"][-1]["status"] == "canceled"
    with allure.step("仓库回补"):
        assert api_client.store_product(store_id, product_id).json()["stock"] == stock_before


@allure.feature("支付异常")
@allure.story("订单取消，重新支付")
@allure.severity(allure.severity_level.CRITICAL)
def test_pay_fail_then_repay_rejected(api_client, login_user, payment_mode):
    with allure.step("fail 模式下下单并支付（订单被取消）"):
        payment_mode("fail")
        api_client.cart_add(STORE2, 5, 1)
        order_id = api_client.order_create().json()["order_id"]
        api_client.order_pay(order_id)
    with allure.step("切回 success，对已取消订单再支付"):
        payment_mode("success")
        resp = api_client.order_pay(order_id)
    with allure.step("断言再次支付被拒（400）"):
        assert_status(resp, 400)


@allure.feature("支付异常")
@allure.story("支付超时")
@allure.severity(allure.severity_level.CRITICAL)
def test_pay_timeout_triggers_retry(base_url, login_user, payment_mode):
    payment_mode("timeout")
    # 用短超时 client 加速（1s < 服务端 6s 挂起）
    client = ApiClient(base_url, timeout=1, retry=2)
    client.token = login_user["token"]
    with allure.step("加购并创建订单"):
        client.cart_add(STORE1, 2, 1)
        order_id = client.order_create().json()["order_id"]
    with allure.step("支付触发超时（预期抛出 Timeout）"):
        with pytest.raises(requests.Timeout):
            client.order_pay(order_id)


@allure.feature("支付异常")
@allure.story("支付失败，仓库回补")
@allure.severity(allure.severity_level.CRITICAL)
def test_pay_fail_restores_all_products_stock(api_client, login_user, payment_mode):
    payment_mode("fail")
    with allure.step("记录两个商品初始库存"):
        stock5_before = api_client.store_product(STORE2, 5).json()["stock"]
        stock6_before = api_client.store_product(STORE2, 6).json()["stock"]
    with allure.step("加购两个商品并下单"):
        api_client.cart_add(STORE2, 5, 2)
        api_client.cart_add(STORE2, 6, 1)
        order_id = api_client.order_create().json()["order_id"]
    with allure.step("支付失败"):
        resp = api_client.order_pay(order_id)
        assert resp.json()["status"] == "failed"
    with allure.step("断言两商品库存均回补"):
        assert api_client.store_product(STORE2, 5).json()["stock"] == stock5_before
        assert api_client.store_product(STORE2, 6).json()["stock"] == stock6_before
