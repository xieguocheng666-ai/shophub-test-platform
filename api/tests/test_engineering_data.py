"""用真实商城验证独立数据、关联场景与清理；只在专用测试库运行。"""
from decimal import Decimal

import pytest

from api.common.assert_util import assert_ok





def test_cart_isolation_with_owned_resources(scenario, db):
    seller = scenario.seller()
    product = scenario.product(seller, price="23.50", stock=9)
    alice, bob = scenario.buyer(), scenario.buyer()
    assert_ok(alice.client.cart_add(product.store_id, product.product_id, 2))
    assert bob.client.cart_get().json()["items"] == []
    assert_ok(bob.client.cart_add(product.store_id, product.product_id, 1))
    assert_ok(alice.client.cart_remove(product.store_id, product.product_id))
    assert [x["quantity"] for x in bob.client.cart_get().json()["items"]] == [1]
    assert db.all("SELECT username,quantity FROM carts WHERE store_id=%s", (product.store_id,)) == [
        {"username": bob.username, "quantity": 1}
    ]


def test_cross_store_paid_order_has_independent_expected_amount(scenario, db):
    buyer = scenario.buyer()
    first = scenario.product(scenario.seller(), price="19.90", stock=5)
    second = scenario.product(scenario.seller(), price="0.10", stock=8)
    expected = Decimal("19.90") * 2 + Decimal("0.10") * 3
    order = scenario.order(buyer, [(first, 2), (second, 3)], state="paid")
    assert db.one("SELECT status,total FROM orders WHERE order_id=%s", (order.order_id,)) == {
        "status": "paid", "total": expected
    }
    assert db.scalar("SELECT COUNT(*) FROM sub_orders WHERE parent_order_id=%s", (order.order_id,)) == 2
    assert db.scalar("SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s", (first.store_id, first.product_id)) == 3
    assert db.scalar("SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s", (second.store_id, second.product_id)) == 5


def test_cleanup_preserves_another_context(base_url, db):
    from test_support.scenarios import TestData

    with TestData(base_url, db) as survivor:
        buyer = survivor.buyer()
        product = survivor.product(survivor.seller(), stock=4)
        order = survivor.order(buyer, [(product, 1)])
        with TestData(base_url, db) as temporary:
            transient_buyer = temporary.buyer()
            transient = temporary.product(temporary.seller())
            temporary.order(transient_buyer, [(transient, 1)], state="paid")
            removed_names = temporary.usernames
            removed_product = transient.product_id
        assert db.scalar("SELECT COUNT(*) FROM products WHERE id=%s", (removed_product,)) == 0
        assert all(db.scalar("SELECT COUNT(*) FROM users WHERE username=%s", (name,)) == 0 for name in removed_names)
        assert db.one("SELECT status,total FROM orders WHERE order_id=%s", (order.order_id,)) == {
            "status": "pending", "total": Decimal("19.90")
        }
        assert db.scalar("SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s", (product.store_id, product.product_id)) == 3
    assert db.scalar("SELECT COUNT(*) FROM users WHERE username=%s", (buyer.username,)) == 0


def test_partial_preparation_failure_still_cleans_created_resources(base_url, db):
    from test_support.scenarios import TestData

    names = ()
    with pytest.raises(ValueError, match="合法商品"):
        with TestData(base_url, db) as data:
            data.buyer()
            data.product(data.seller())
            names = data.usernames
            data.product(data.seller(), price="-1")
    assert names
    assert all(db.scalar("SELECT COUNT(*) FROM users WHERE username=%s", (name,)) == 0 for name in names)


def test_repeated_cleanup_does_not_remove_fixed_seed_users(base_url, db):
    from test_support.scenarios import TestData

    before = db.all("SELECT username,role FROM users WHERE username IN ('alice','bob','seller1','seller2') ORDER BY username")
    data = TestData(base_url, db)
    data.product(data.seller())
    data.cleanup()
    data.cleanup()
    assert db.all("SELECT username,role FROM users WHERE username IN ('alice','bob','seller1','seller2') ORDER BY username") == before


def test_product_factory_rejects_existing_shared_name(base_url, db):
    from test_support.scenarios import TestData

    with TestData(base_url, db) as first:
        product = first.product(first.seller(), name=f"shared_{first.factory.namespace}")
        with pytest.raises(ValueError, match="已存在"):
            with TestData(base_url, db) as second:
                second.product(second.seller(), name=f"shared_{first.factory.namespace}")
        assert db.scalar("SELECT COUNT(*) FROM products WHERE id=%s", (product.product_id,)) == 1
        assert db.scalar("SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s", (product.store_id, product.product_id)) == 5


def test_preexisting_unlinked_product_is_not_factory_owned(base_url, db):
    from uuid import uuid4
    from test_support.scenarios import TestData

    name = f"preexisting_{uuid4().hex}"
    with db.conn.cursor() as cursor:
        cursor.execute("INSERT INTO products (name) VALUES (%s)", (name,))
        original_id = cursor.lastrowid
    try:
        with pytest.raises(ValueError, match="已存在"):
            with TestData(base_url, db) as data:
                data.product(data.seller(), name=name)
        assert db.scalar("SELECT COUNT(*) FROM products WHERE id=%s", (original_id,)) == 1
    finally:
        with db.conn.cursor() as cursor:
            cursor.execute("DELETE FROM products WHERE id=%s", (original_id,))
