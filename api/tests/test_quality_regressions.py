"""输入校验、用户隔离与超时筛选的真实接口回归；运行须使用专用测试库。"""
from decimal import Decimal

import pytest

from api.common.assert_util import assert_error, assert_ok


def _login(api_client, config, name):
    user = config["users"][name]
    assert_ok(api_client.login(user["username"], user["password"]))


def _business_snapshot(db):
    return {
        "stocks": db.all("SELECT * FROM store_product ORDER BY store_id, product_id"),
        "orders": db.all("SELECT * FROM orders ORDER BY order_id"),
        "subs": db.all("SELECT * FROM sub_orders ORDER BY sub_order_id"),
        "items": db.all("SELECT * FROM order_items ORDER BY id"),
        "carts": db.all("SELECT * FROM carts ORDER BY username, store_id, product_id"),
    }


@pytest.mark.parametrize("price,stock", [(-1, 1), (1, -1)])
def test_product_negative_values_rejected_without_writes(price, stock, api_client, config, db):
    _login(api_client, config, "seller1")
    before_products = db.all("SELECT * FROM products ORDER BY id")
    before = _business_snapshot(db)
    response = api_client.add_product(1, "invalid product", price, stock)
    assert response.status_code == 422, response.text
    assert db.all("SELECT * FROM products ORDER BY id") == before_products
    assert _business_snapshot(db) == before


def test_stock_negative_value_rejected_without_writes(api_client, config, db):
    _login(api_client, config, "seller1")
    before = _business_snapshot(db)
    response = api_client.update_stock(1, 2, -1)
    assert response.status_code == 422, response.text
    assert _business_snapshot(db) == before


def test_stock_same_value_update_succeeds(api_client, config, db):
    _login(api_client, config, "seller1")
    before = _business_snapshot(db)
    assert_ok(api_client.update_stock(1, 2, 100))
    assert_ok(api_client.update_stock(1, 2, 100))
    assert _business_snapshot(db) == before


def test_stock_missing_product_rejected(api_client, config, db):
    _login(api_client, config, "seller1")
    before = _business_snapshot(db)
    assert_error(api_client.update_stock(1, 999999, 100), 404, "店铺中没有该商品")
    assert _business_snapshot(db) == before


def test_zero_price_and_stock_are_accepted(api_client, config, db):
    _login(api_client, config, "seller1")
    response = api_client.add_product(1, "zero boundary product", 0, 0)
    assert_ok(response)
    product_id = response.json()["product_id"]
    assert db.one("SELECT price, stock FROM store_product WHERE store_id=1 AND product_id=%s", (product_id,)) == {
        "price": Decimal("0.00"), "stock": 0,
    }
    assert_ok(api_client.update_stock(1, product_id, 0))


def test_cart_isolated_between_users(api_client, config, db):
    _login(api_client, config, "alice")
    assert_ok(api_client.cart_add(1, 2, 2))
    _login(api_client, config, "bob")
    response = api_client.cart_get()
    assert_ok(response)
    assert response.json()["items"] == []
    assert_ok(api_client.cart_add(1, 2, 1))
    assert [item["quantity"] for item in api_client.cart_get().json()["items"]] == [1]
    _login(api_client, config, "alice")
    assert [item["quantity"] for item in api_client.cart_get().json()["items"]] == [2]
    assert_ok(api_client.cart_remove(1, 2))
    _login(api_client, config, "bob")
    assert [item["quantity"] for item in api_client.cart_get().json()["items"]] == [1]
    assert db.all("SELECT username, quantity FROM carts ORDER BY username") == [{"username": "bob", "quantity": 1}]
    assert db.scalar("SELECT stock FROM store_product WHERE store_id=1 AND product_id=2") == 100


def test_confirm_other_buyers_shipped_order_has_no_side_effect(api_client, config, db):
    _login(api_client, config, "alice")
    assert_ok(api_client.cart_add(1, 2, 1))
    response = api_client.order_create()
    assert_ok(response)
    order = response.json()
    sub_id = order["sub_orders"][0]["sub_order_id"]
    assert_ok(api_client.order_pay(order["order_id"]))
    _login(api_client, config, "seller1")
    assert_ok(api_client.sub_order_ship(sub_id))
    _login(api_client, config, "bob")
    before = _business_snapshot(db)
    assert_error(api_client.sub_order_confirm(sub_id), 403, "无权操作他人订单")
    assert _business_snapshot(db) == before


def test_cancel_expired_only_cancels_old_pending(api_client, login_user, db):
    # 三笔订单同商品、各一件；支付不再扣库存。
    stock_before = db.scalar("SELECT stock FROM store_product WHERE store_id=1 AND product_id=2")
    order_ids = []
    for _ in range(3):
        assert_ok(api_client.cart_add(1, 2, 1))
        response = api_client.order_create()
        assert_ok(response)
        order_ids.append(response.json()["order_id"])
    old_pending, recent_pending, old_paid = order_ids
    assert_ok(api_client.order_pay(old_paid))
    # 仅造本例订单的创建时间，远离 1 小时临界点，不使用真实等待。
    with db.conn.cursor() as cursor:
        cursor.execute(
            "UPDATE orders SET created_at=NOW()-INTERVAL 2 HOUR WHERE order_id IN (%s,%s)",
            (old_pending, old_paid),
        )
    before_items = db.all("SELECT * FROM order_items ORDER BY id")
    response = api_client.cancel_expired(3600)
    assert_ok(response)
    assert response.json()["cancelled"] == 1
    expected = {old_pending: "canceled", recent_pending: "pending", old_paid: "paid"}
    assert {row["order_id"]: row["status"] for row in db.all("SELECT order_id,status FROM orders")} == expected
    assert {row["parent_order_id"]: row["status"] for row in db.all("SELECT parent_order_id,status FROM sub_orders")} == expected
    assert db.scalar("SELECT stock FROM store_product WHERE store_id=1 AND product_id=2") == stock_before - 2
    assert db.all("SELECT * FROM order_items ORDER BY id") == before_items
    response = api_client.cancel_expired(3600)
    assert_ok(response)
    assert response.json()["cancelled"] == 0
    assert db.scalar("SELECT stock FROM store_product WHERE store_id=1 AND product_id=2") == stock_before - 2
