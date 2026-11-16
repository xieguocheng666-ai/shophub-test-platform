"""输入校验、用户隔离与超时筛选的真实接口回归；运行须使用专用测试库。"""
from decimal import Decimal
import pytest
from api.common.assert_util import assert_error, assert_ok

def _login(api_client, config, name):
    user = config['users'][name]
    assert_ok(api_client.login(user['username'], user['password']))

def _business_snapshot(db, catalog):
    return {'stocks': db.all(f'SELECT * FROM store_product WHERE store_id IN ({catalog.s1},{catalog.s2}) ORDER BY store_id, product_id'), 'orders': db.all(f"SELECT * FROM orders WHERE username IN ('{catalog.alice.username}','{catalog.bob.username}') ORDER BY order_id"), 'subs': db.all(f'SELECT * FROM sub_orders WHERE store_id IN ({catalog.s1},{catalog.s2}) ORDER BY sub_order_id'), 'items': db.all(f'SELECT * FROM order_items WHERE product_id IN ({catalog.product_ids_sql}) ORDER BY id'), 'carts': db.all(f"SELECT * FROM carts WHERE username IN ('{catalog.alice.username}','{catalog.bob.username}') ORDER BY username, store_id, product_id")}

@pytest.mark.parametrize('price,stock', [(-1, 1), (1, -1)])
def test_product_negative_values_rejected_without_writes(price, stock, api_client, config, db, catalog):
    _login(api_client, config, 'seller1')
    before_products = db.all('SELECT * FROM products WHERE name=%s', (catalog.invalid_name,))
    before = _business_snapshot(db, catalog)
    response = api_client.add_product(catalog.s1, catalog.invalid_name, price, stock)
    assert response.status_code == 422, response.text
    assert db.all('SELECT * FROM products WHERE name=%s', (catalog.invalid_name,)) == before_products
    assert _business_snapshot(db, catalog) == before

def test_stock_negative_value_rejected_without_writes(api_client, config, db, catalog):
    _login(api_client, config, 'seller1')
    before = _business_snapshot(db, catalog)
    response = api_client.update_stock(catalog.s1, catalog.p2, -1)
    assert response.status_code == 422, response.text
    assert _business_snapshot(db, catalog) == before

def test_stock_same_value_update_succeeds(api_client, config, db, catalog):
    _login(api_client, config, 'seller1')
    before = _business_snapshot(db, catalog)
    assert_ok(api_client.update_stock(catalog.s1, catalog.p2, 100))
    assert_ok(api_client.update_stock(catalog.s1, catalog.p2, 100))
    assert _business_snapshot(db, catalog) == before

def test_stock_missing_product_rejected(api_client, config, db, catalog):
    _login(api_client, config, 'seller1')
    before = _business_snapshot(db, catalog)
    assert_error(api_client.update_stock(catalog.s1, catalog.missing_product, 100), 404, '店铺中没有该商品')
    assert _business_snapshot(db, catalog) == before

def test_zero_price_and_stock_are_accepted(api_client, config, db, catalog):
    _login(api_client, config, 'seller1')
    response = api_client.add_product(catalog.s1, catalog.zero_name, 0, 0)
    assert_ok(response)
    product_id = response.json()['product_id']
    assert db.one(f'SELECT price, stock FROM store_product WHERE store_id={catalog.s1} AND product_id=%s', (product_id,)) == {'price': Decimal('0.00'), 'stock': 0}
    assert_ok(api_client.update_stock(catalog.s1, product_id, 0))

def test_cart_isolated_between_users(scenario, db):
    alice, bob = (scenario.buyer(), scenario.buyer())
    product = scenario.product(scenario.seller(), stock=100)
    assert_ok(alice.client.cart_add(product.store_id, product.product_id, 2))
    response = bob.client.cart_get()
    assert_ok(response)
    assert response.json()['items'] == []
    assert_ok(bob.client.cart_add(product.store_id, product.product_id, 1))
    assert [item['quantity'] for item in bob.client.cart_get().json()['items']] == [1]
    assert [item['quantity'] for item in alice.client.cart_get().json()['items']] == [2]
    assert_ok(alice.client.cart_remove(product.store_id, product.product_id))
    assert [item['quantity'] for item in bob.client.cart_get().json()['items']] == [1]
    assert db.all('SELECT username,quantity FROM carts WHERE store_id=%s', (product.store_id,)) == [{'username': bob.username, 'quantity': 1}]
    assert db.scalar('SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s', (product.store_id, product.product_id)) == 100

def test_confirm_other_buyers_shipped_order_has_no_side_effect(api_client, config, db, catalog):
    _login(api_client, config, 'alice')
    assert_ok(api_client.cart_add(catalog.s1, catalog.p2, 1))
    response = api_client.order_create()
    assert_ok(response)
    order = response.json()
    sub_id = order['sub_orders'][0]['sub_order_id']
    assert_ok(api_client.order_pay(order['order_id']))
    _login(api_client, config, 'seller1')
    assert_ok(api_client.sub_order_ship(sub_id))
    _login(api_client, config, 'bob')
    before = _business_snapshot(db, catalog)
    assert_error(api_client.sub_order_confirm(sub_id), 403, '无权操作他人订单')
    assert _business_snapshot(db, catalog) == before

def test_cancel_expired_only_cancels_old_pending(api_client, login_user, db, catalog):
    stock_before = db.scalar(f'SELECT stock FROM store_product WHERE store_id={catalog.s1} AND product_id={catalog.p2}')
    order_ids = []
    for _ in range(3):
        assert_ok(api_client.cart_add(catalog.s1, catalog.p2, 1))
        response = api_client.order_create()
        assert_ok(response)
        order_ids.append(response.json()['order_id'])
    old_pending, recent_pending, old_paid = order_ids
    assert_ok(api_client.order_pay(old_paid))
    with db.conn.cursor() as cursor:
        cursor.execute('UPDATE orders SET created_at=NOW()-INTERVAL 2 HOUR WHERE order_id IN (%s,%s)', (old_pending, old_paid))
    before_items = db.all(f'SELECT * FROM order_items WHERE product_id IN ({catalog.product_ids_sql}) ORDER BY id')
    response = api_client.cancel_expired(3600)
    assert_ok(response)
    assert response.json()['cancelled'] == 1
    expected = {old_pending: 'canceled', recent_pending: 'pending', old_paid: 'paid'}
    assert {row['order_id']: row['status'] for row in db.all(f"SELECT order_id,status FROM orders WHERE username IN ('{catalog.alice.username}','{catalog.bob.username}')")} == expected
    assert {row['parent_order_id']: row['status'] for row in db.all(f'SELECT parent_order_id,status FROM sub_orders WHERE store_id IN ({catalog.s1},{catalog.s2})')} == expected
    assert db.scalar(f'SELECT stock FROM store_product WHERE store_id={catalog.s1} AND product_id={catalog.p2}') == stock_before - 2
    assert db.all(f'SELECT * FROM order_items WHERE product_id IN ({catalog.product_ids_sql}) ORDER BY id') == before_items
    response = api_client.cancel_expired(3600)
    assert_ok(response)
    assert response.json()['cancelled'] == 0
    assert db.scalar(f'SELECT stock FROM store_product WHERE store_id={catalog.s1} AND product_id={catalog.p2}') == stock_before - 2
