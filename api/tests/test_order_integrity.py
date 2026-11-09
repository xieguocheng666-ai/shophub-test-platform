"""订单一致性：失败原子性、重复操作及多子单聚合。

每条用例用真实接口准备数据，以独立数据库查询核对副作用。
"""
from decimal import Decimal

import allure
import pytest

from api.api_client import ApiClient
from api.common.assert_util import assert_error, assert_ok


def _snapshot(db):
    """失败前后逐行比较；保留主键，避免只数行数漏掉错误改写。"""
    return {
        "stocks": db.all("SELECT store_id, product_id, price, stock FROM store_product ORDER BY store_id, product_id"),
        "orders": db.all("SELECT * FROM orders ORDER BY order_id"),
        "subs": db.all("SELECT * FROM sub_orders ORDER BY sub_order_id"),
        "items": db.all("SELECT * FROM order_items ORDER BY id"),
        "carts": db.all("SELECT * FROM carts ORDER BY username, store_id, product_id"),
    }


@allure.feature("下单一致性")
@allure.story("加购后库存变化，下单失败不留下半成品")
@pytest.mark.parametrize("multiple", [False, True], ids=["single-product", "rollback-earlier-product"])
def test_order_stock_changed_rolls_back(multiple, api_client, login_user, base_url, config, db):
    # SQL 按商品号排序扣库存：商品 2 的扣减必须在商品 3 不足时撤销。
    if multiple:
        assert_ok(api_client.cart_add(1, 2, 2))
    assert_ok(api_client.cart_add(1, 3, 2))
    seller = ApiClient(base_url, retry=0)
    try:
        user = config["users"]["seller1"]
        assert_ok(seller.login(user["username"], user["password"]))
        assert_ok(seller.update_stock(1, 3, 1))
    finally:
        seller.session.close()
    before = _snapshot(db)
    assert_error(api_client.order_create(), 400, "库存不足")
    assert _snapshot(db) == before, "失败下单必须整体回滚，购物车、库存和订单均保持请求前状态"


@allure.feature("支付一致性")
@allure.story("已支付订单再次支付不重复生效")
def test_paid_order_repay_has_no_side_effect(api_client, login_user, db):
    assert_ok(api_client.cart_add(1, 2, 2))
    order = api_client.order_create()
    assert_ok(order)
    order_id = order.json()["order_id"]
    assert_ok(api_client.order_pay(order_id))
    assert db.scalar("SELECT status FROM orders WHERE order_id=%s", (order_id,)) == "paid"
    before = _snapshot(db)
    assert_error(api_client.order_pay(order_id), 400, "订单状态不允许支付")
    assert _snapshot(db) == before


@allure.feature("状态机")
@allure.story("不允许的状态操作及无副作用")
@pytest.mark.parametrize(
    "operation,start_state,detail",
    [("ship", "pending", "不允许从 pending 变为 shipped"), ("confirm", "pending", "pending"),
     ("confirm", "paid", "paid"), ("ship", "shipped", "不允许从 shipped 变为 shipped"),
     ("confirm", "completed", "completed")],
)
def test_invalid_order_actions_preserve_data(operation, start_state, detail, api_client, login_user, config, db):
    assert_ok(api_client.cart_add(1, 2, 2))
    response = api_client.order_create()
    assert_ok(response)
    order = response.json()
    sub_id = order["sub_orders"][0]["sub_order_id"]
    if start_state != "pending":
        assert_ok(api_client.order_pay(order["order_id"]))
    if start_state in ("shipped", "completed"):
        seller = config["users"]["seller1"]
        assert_ok(api_client.login(seller["username"], seller["password"]))
        assert_ok(api_client.sub_order_ship(sub_id))
        alice = config["users"]["alice"]
        assert_ok(api_client.login(alice["username"], alice["password"]))
    if start_state == "completed":
        assert_ok(api_client.sub_order_confirm(sub_id))
    if operation == "ship":
        seller = config["users"]["seller1"]
        assert_ok(api_client.login(seller["username"], seller["password"]))
    assert db.scalar("SELECT status FROM sub_orders WHERE sub_order_id=%s", (sub_id,)) == start_state
    before = _snapshot(db)
    response = api_client.sub_order_ship(sub_id) if operation == "ship" else api_client.sub_order_confirm(sub_id)
    assert_error(response, 400, detail)
    assert _snapshot(db) == before


@allure.feature("拆单")
@allure.story("只收货一部分时父订单不能提前完成")
def test_parent_completes_only_after_all_suborders(api_client, login_user, config, db):
    for store, product in [(1, 2), (2, 5)]:
        assert_ok(api_client.cart_add(store, product, 1))
    response = api_client.order_create()
    assert_ok(response)
    order = response.json()
    assert len(order["sub_orders"]) == 2
    subs = {s["store_id"]: s["sub_order_id"] for s in order["sub_orders"]}
    assert_ok(api_client.order_pay(order["order_id"]))
    for store in (1, 2):
        user = config["users"][f"seller{store}"]
        assert_ok(api_client.login(user["username"], user["password"]))
        assert_ok(api_client.sub_order_ship(subs[store]))
    user = config["users"]["alice"]
    assert_ok(api_client.login(user["username"], user["password"]))
    assert_ok(api_client.sub_order_confirm(subs[1]))
    assert db.scalar("SELECT status FROM sub_orders WHERE sub_order_id=%s", (subs[1],)) == "completed"
    assert db.scalar("SELECT status FROM sub_orders WHERE sub_order_id=%s", (subs[2],)) == "shipped"
    assert db.scalar("SELECT status FROM orders WHERE order_id=%s", (order["order_id"],)) == "paid"
    assert_ok(api_client.sub_order_confirm(subs[2]))
    assert db.scalar("SELECT status FROM orders WHERE order_id=%s", (order["order_id"],)) == "completed"


@allure.feature("支付一致性")
@allure.story("跨店支付失败后所有子单取消且库存仅回补一次")
def test_cross_store_failure_preserves_amount_and_items(api_client, login_user, payment_mode, db):
    payment_mode("fail")
    # 用小数单价、多件数量检查金额；预期在下单之前计算。
    prices = {p: db.scalar("SELECT price FROM store_product WHERE store_id=2 AND product_id=%s", (p,)) for p in (5, 6)}
    expected_total = prices[5] * 2 + prices[6]
    stocks = db.all("SELECT store_id, product_id, stock FROM store_product ORDER BY store_id, product_id")
    # 增加店铺 1 商品，让失败处理确实跨两个店铺。
    keyboard_price = db.scalar("SELECT price FROM store_product WHERE store_id=1 AND product_id=2")
    expected_by_store = {1: keyboard_price, 2: expected_total}
    expected_total += keyboard_price
    for store, product, qty in [(1, 2, 1), (2, 5, 2), (2, 6, 1)]:
        assert_ok(api_client.cart_add(store, product, qty))
    response = api_client.order_create()
    assert_ok(response)
    order = response.json()
    assert Decimal(str(order["total"])) == expected_total
    assert {s["store_id"]: Decimal(str(s["total"])) for s in order["sub_orders"]} == expected_by_store
    assert {s["store_id"]: s["total"] for s in db.all(
        "SELECT store_id, total FROM sub_orders WHERE parent_order_id=%s", (order["order_id"],)
    )} == expected_by_store
    actual_items = db.all(
        "SELECT so.store_id, oi.product_id, oi.price, oi.quantity FROM order_items oi "
        "JOIN sub_orders so ON oi.sub_order_id=so.sub_order_id "
        "WHERE so.parent_order_id=%s ORDER BY so.store_id, oi.product_id",
        (order["order_id"],),
    )
    assert actual_items == [
        {"store_id": 1, "product_id": 2, "price": keyboard_price, "quantity": 1},
        {"store_id": 2, "product_id": 5, "price": prices[5], "quantity": 2},
        {"store_id": 2, "product_id": 6, "price": prices[6], "quantity": 1},
    ]
    items_before = db.all("SELECT * FROM order_items ORDER BY id")
    assert len(items_before) == 3
    response = api_client.order_pay(order["order_id"])
    assert_ok(response)
    assert response.json()["status"] == "failed"
    assert db.one("SELECT status, total FROM orders WHERE order_id=%s", (order["order_id"],)) == {"status": "canceled", "total": expected_total}
    assert db.scalar("SELECT COUNT(*) FROM sub_orders WHERE parent_order_id=%s AND status='canceled'", (order["order_id"],)) == 2
    assert db.all("SELECT * FROM order_items ORDER BY id") == items_before
    assert db.all("SELECT store_id, product_id, stock FROM store_product ORDER BY store_id, product_id") == stocks
    after_failure = _snapshot(db)
    assert_error(api_client.order_pay(order["order_id"]), 400, "订单状态不允许支付")
    assert _snapshot(db) == after_failure
