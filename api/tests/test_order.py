"""订单状态机 + 支付异常测试：全流程、非法流转、越权、支付失败/超时。"""
import allure
import pytest
import requests
from decimal import Decimal

from api.api_client import ApiClient
from api.common.assert_util import assert_error, assert_ok
from api.common.db_util import assert_db_value
from api.tests.constants import STORE1, STORE2


# ---------- 状态机 ----------
@allure.feature("状态机")
@allure.story("全流程")
@allure.severity(allure.severity_level.CRITICAL)
def test_order_full_flow(api_client, login_user, config, db):
    seller1 = config["users"]["seller1"]
    alice = config["users"]["alice"]

    with allure.step("加购商品到购物车"):
        product = db.one("SELECT price, stock FROM store_product WHERE store_id=1 AND product_id=2")
        quantity = 2
        expected_total = product["price"] * quantity
        expected_stock = product["stock"] - quantity
        assert_ok(api_client.cart_add(STORE1, 2, quantity))
        assert db.scalar("SELECT stock FROM store_product WHERE store_id=1 AND product_id=2") == product["stock"]

    with allure.step("创建订单"):
        order_resp = api_client.order_create()
        assert_ok(order_resp)
        order_id = order_resp.json()["order_id"]
        sub_order_id = order_resp.json()["sub_orders"][0]["sub_order_id"]
        assert order_resp.json()["status"] == "pending"
        assert Decimal(str(order_resp.json()["total"])) == expected_total
        assert db.one("SELECT username, status, total FROM orders WHERE order_id=%s", (order_id,)) == {
            "username": login_user["username"], "status": "pending", "total": expected_total,
        }
        assert db.all("SELECT store_id, status, total FROM sub_orders WHERE parent_order_id=%s", (order_id,)) == [
            {"store_id": STORE1, "status": "pending", "total": expected_total}
        ]
        assert db.all("SELECT product_id, price, quantity FROM order_items WHERE sub_order_id=%s", (sub_order_id,)) == [
            {"product_id": 2, "price": product["price"], "quantity": quantity}
        ]
        assert db.scalar("SELECT stock FROM store_product WHERE store_id=1 AND product_id=2") == expected_stock
        assert db.scalar("SELECT COUNT(*) FROM carts WHERE username=%s", (login_user["username"],)) == 0

    with allure.step("支付订单"):
        pay_resp = api_client.order_pay(order_id)
        assert_ok(pay_resp)
        assert pay_resp.json()["status"] == "paid"
        assert db.scalar("SELECT status FROM orders WHERE order_id=%s", (order_id,)) == "paid"
        assert db.scalar("SELECT status FROM sub_orders WHERE sub_order_id=%s", (sub_order_id,)) == "paid"
        assert db.scalar("SELECT stock FROM store_product WHERE store_id=1 AND product_id=2") == expected_stock
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
        ship_resp = api_client.sub_order_ship(sub_order_id)
        assert_ok(ship_resp)
        assert ship_resp.json()["status"] == "shipped"
        assert db.scalar("SELECT status FROM sub_orders WHERE sub_order_id=%s", (sub_order_id,)) == "shipped"
    with allure.step("买家确认收货"):
        api_client.login(alice["username"], alice["password"])
        confirm_resp = api_client.sub_order_confirm(sub_order_id)
        assert_ok(confirm_resp)
        assert confirm_resp.json()["status"] == "completed"

    with allure.step("直连数据库确认最终状态已落库"):
        assert_db_value(
            db, "SELECT status FROM orders WHERE order_id=%s",
            "completed", (order_id,), "父订单最终状态",
        )
        assert_db_value(
            db,
            "SELECT COUNT(*) AS n FROM sub_orders WHERE parent_order_id=%s AND status<>'completed'",
            0, (order_id,), "子订单应全部完成",
        )
        assert_db_value(
            db,
            "SELECT SUM(oi.quantity) AS n FROM order_items oi "
            "JOIN sub_orders so ON oi.sub_order_id=so.sub_order_id "
            "WHERE so.parent_order_id=%s",
            quantity, (order_id,), "订单明细数量",
        )
        assert db.scalar("SELECT stock FROM store_product WHERE store_id=1 AND product_id=2") == expected_stock
        assert db.scalar("SELECT total FROM orders WHERE order_id=%s", (order_id,)) == expected_total


@allure.feature("状态机")
@allure.story("非法状态流转")
@allure.severity(allure.severity_level.NORMAL)
def test_illegal_transition_rejected(api_client, login_user, db):
    with allure.step("加购商品"):
        api_client.cart_add(STORE1, 3, 1)
    with allure.step("创建订单"):
        sub_order_id = api_client.order_create().json()["sub_orders"][0]["sub_order_id"]
    with allure.step("待支付直接确认收货（预期 400）"):
        resp = api_client.sub_order_confirm(sub_order_id)
        assert_error(resp, 400, "不允许从 pending 变为 completed")
    with allure.step("确认被拒后状态没有被误改"):
        assert_db_value(
            db, "SELECT status FROM sub_orders WHERE sub_order_id=%s",
            "pending", (sub_order_id,), "非法流转不应改变子订单状态",
        )


@allure.feature("状态机")
@allure.story("水平越权")
@allure.severity(allure.severity_level.NORMAL)
def test_cannot_operate_others_order(api_client, config, db):
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
        assert_error(resp, 404, "订单不存在")
    with allure.step("确认越权支付没有产生副作用"):
        assert_db_value(
            db, "SELECT status FROM orders WHERE order_id=%s",
            "pending", (order_id,), "越权支付不应改变订单状态",
        )


@allure.feature("状态机")
@allure.story("垂直越权")
@allure.severity(allure.severity_level.NORMAL)
def test_buyer_cannot_ship(api_client, config, db):
    alice = config["users"]["alice"]
    with allure.step("alice 登录并创建订单"):
        api_client.login(alice["username"], alice["password"])
        api_client.cart_add(STORE1, 2, 1)
        sub_order_id = api_client.order_create().json()["sub_orders"][0]["sub_order_id"]

    with allure.step("买家尝试发货（无 seller 角色，预期 403）"):
        resp = api_client.sub_order_ship(sub_order_id)
    with allure.step("断言垂直越权被拒（403）"):
        assert_error(resp, 403, "需要商家权限")
    with allure.step("确认越权发货没有产生副作用"):
        assert_db_value(
            db, "SELECT status FROM sub_orders WHERE sub_order_id=%s",
            "pending", (sub_order_id,), "越权发货不应改变子订单状态",
        )


# ---------- 支付异常 ----------
@allure.feature("支付异常")
@allure.story("订单取消，仓库回补")
@allure.severity(allure.severity_level.CRITICAL)
def test_pay_fail_cancels_and_restores_stock(api_client, login_user, payment_mode, db):
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
        assert next(o for o in api_client.orders().json()["orders"] if o["order_id"] == order_id)["status"] == "canceled"
        assert_db_value(
            db, "SELECT status FROM orders WHERE order_id=%s",
            "canceled", (order_id,), "支付失败后父订单状态",
        )
    with allure.step("仓库回补"):
        assert api_client.store_product(store_id, product_id).json()["stock"] == stock_before
        assert_db_value(
            db,
            "SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s",
            stock_before, (store_id, product_id), "支付失败后库存应回补",
        )
    with allure.step("失败订单的明细仍应保留"):
        assert_db_value(
            db,
            "SELECT SUM(oi.quantity) AS n FROM order_items oi "
            "JOIN sub_orders so ON oi.sub_order_id=so.sub_order_id "
            "WHERE so.parent_order_id=%s",
            2, (order_id,), "订单明细数量",
        )


@allure.feature("支付异常")
@allure.story("订单取消，重新支付")
@allure.severity(allure.severity_level.CRITICAL)
def test_pay_fail_then_repay_rejected(api_client, login_user, payment_mode, db):
    stock_before = db.scalar(
        "SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s", (STORE2, 5)
    )
    with allure.step("fail 模式下下单并支付（订单被取消）"):
        payment_mode("fail")
        api_client.cart_add(STORE2, 5, 1)
        order_id = api_client.order_create().json()["order_id"]
        api_client.order_pay(order_id)
    with allure.step("切回 success，对已取消订单再支付"):
        payment_mode("success")
        resp = api_client.order_pay(order_id)
    with allure.step("断言再次支付被拒（400）"):
        assert_error(resp, 400, "订单状态不允许支付")
    with allure.step("确认没有错误改状态或二次回补（本项目无真实扣款）"):
        assert_db_value(
            db, "SELECT status FROM orders WHERE order_id=%s",
            "canceled", (order_id,), "重复支付不应把订单改回已支付",
        )
        assert_db_value(
            db, "SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s",
            stock_before, (STORE2, 5), "重复支付不应再次回补库存",
        )


@allure.feature("支付异常")
@allure.story("支付超时")
@allure.severity(allure.severity_level.CRITICAL)
def test_pay_timeout_preserves_order_and_stock(base_url, login_user, payment_mode, db):
    payment_mode("timeout")
    stock_before = db.scalar(
        "SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s", (STORE1, 2)
    )
    # 用短超时 client 加速（1s < 服务端 6s 挂起）
    client = ApiClient(base_url, timeout=1, retry=0)
    client.token = login_user["token"]
    with allure.step("加购并创建订单"):
        client.cart_add(STORE1, 2, 1)
        order_id = client.order_create().json()["order_id"]
    with allure.step("支付触发超时（预期抛出 Timeout）"):
        with pytest.raises(requests.Timeout):
            client.order_pay(order_id)
    with allure.step("确认超时没有误改订单或库存"):
        assert_db_value(
            db, "SELECT status FROM orders WHERE order_id=%s",
            "pending", (order_id,), "支付超时后订单应仍为待支付",
        )
        assert_db_value(
            db,
            "SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s",
            stock_before - 1, (STORE1, 2), "下单已扣库存，支付超时不应回补",
        )


@allure.feature("支付异常")
@allure.story("支付失败，仓库回补")
@allure.severity(allure.severity_level.CRITICAL)
def test_pay_fail_restores_all_products_stock(api_client, login_user, payment_mode, db):
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
        assert_db_value(
            db, "SELECT stock FROM store_product WHERE store_id=%s AND product_id=5",
            stock5_before, (STORE2,), "商品 5 库存回补",
        )
        assert_db_value(
            db, "SELECT stock FROM store_product WHERE store_id=%s AND product_id=6",
            stock6_before, (STORE2,), "商品 6 库存回补",
        )


@allure.feature("支付异常")
@allure.story("超时关单幂等：重复触发只回补一次")
@allure.severity(allure.severity_level.CRITICAL)
def test_cancel_expired_idempotent(api_client, login_user, db):
    store_id, product_id = STORE1, 2
    stock_before = db.scalar(
        "SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s",
        (store_id, product_id),
    )
    with allure.step("加购 2 件并下单，库存先被扣掉"):
        api_client.cart_add(store_id, product_id, 2)
        order_id = api_client.order_create().json()["order_id"]
        assert_db_value(
            db, "SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s",
            stock_before - 2, (store_id, product_id), "下单后库存应扣减",
        )

    with allure.step("第一次触发超时关单"):
        assert api_client.cancel_expired(0).json()["cancelled"] == 1
    with allure.step("第二次触发超时关单（幂等）"):
        assert api_client.cancel_expired(0).json()["cancelled"] == 0

    with allure.step("确认只回补一次、订单只取消一次"):
        assert_db_value(
            db, "SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s",
            stock_before, (store_id, product_id), "重复关单不应二次回补库存",
        )
        assert_db_value(
            db, "SELECT status FROM orders WHERE order_id=%s",
            "canceled", (order_id,), "订单应只被取消一次",
        )
        assert_db_value(
            db,
            "SELECT COUNT(*) AS n FROM sub_orders WHERE parent_order_id=%s AND status='canceled'",
            1, (order_id,), "子订单应同步取消",
        )
