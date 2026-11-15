"""订单一致性：失败原子性、重复操作及多子单聚合。

每条用例用真实接口准备数据，以独立数据库查询核对副作用。
"""
from decimal import Decimal
import allure
import pytest
from api.api_client import ApiClient
from api.common.assert_util import assert_error, assert_ok

def _snapshot(db, catalog):
    """失败前后逐行比较；保留主键，避免只数行数漏掉错误改写。"""
    return {'stocks': db.all(f'SELECT store_id, product_id, price, stock FROM store_product WHERE store_id IN ({catalog.s1},{catalog.s2}) ORDER BY store_id, product_id'), 'orders': db.all(f"SELECT * FROM orders WHERE username IN ('{catalog.alice.username}','{catalog.bob.username}') ORDER BY order_id"), 'subs': db.all(f'SELECT * FROM sub_orders WHERE store_id IN ({catalog.s1},{catalog.s2}) ORDER BY sub_order_id'), 'items': db.all(f'SELECT * FROM order_items WHERE product_id IN ({catalog.product_ids_sql}) ORDER BY id'), 'carts': db.all(f"SELECT * FROM carts WHERE username IN ('{catalog.alice.username}','{catalog.bob.username}') ORDER BY username, store_id, product_id")}

@allure.feature('下单一致性')
@allure.story('加购后库存变化，下单失败不留下半成品')
@pytest.mark.parametrize('multiple', [False, True], ids=['single-product', 'rollback-earlier-product'])
def test_order_stock_changed_rolls_back(multiple, api_client, login_user, base_url, config, db, catalog):
    # 本例p2先于p3创建；扣库存时p2的成功更新必须在p3不足时回滚。
    if multiple:
        assert_ok(api_client.cart_add(catalog.s1, catalog.p2, 2))
    assert_ok(api_client.cart_add(catalog.s1, catalog.p3, 2))
    seller = ApiClient(base_url, retry=0)
    try:
        user = config['users']['seller1']
        assert_ok(seller.login(user['username'], user['password']))
        assert_ok(seller.update_stock(catalog.s1, catalog.p3, 1))
    finally:
        seller.session.close()
    before = _snapshot(db, catalog)
    assert_error(api_client.order_create(), 400, '库存不足')
    assert _snapshot(db, catalog) == before, '失败下单必须整体回滚，购物车、库存和订单均保持请求前状态'

@allure.feature('支付一致性')
@allure.story('已支付订单再次支付不重复生效')
def test_paid_order_repay_has_no_side_effect(api_client, login_user, db, catalog):
    assert_ok(api_client.cart_add(catalog.s1, catalog.p2, 2))
    order = api_client.order_create()
    assert_ok(order)
    order_id = order.json()['order_id']
    assert_ok(api_client.order_pay(order_id))
    assert db.scalar('SELECT status FROM orders WHERE order_id=%s', (order_id,)) == 'paid'
    before = _snapshot(db, catalog)
    assert_error(api_client.order_pay(order_id), 400, '订单状态不允许支付')
    assert _snapshot(db, catalog) == before

@allure.feature('状态机')
@allure.story('不允许的状态操作及无副作用')
@pytest.mark.parametrize('operation,start_state,detail', [('ship', 'pending', '不允许从 pending 变为 shipped'), ('confirm', 'pending', 'pending'), ('confirm', 'paid', 'paid'), ('ship', 'shipped', '不允许从 shipped 变为 shipped'), ('confirm', 'completed', 'completed')])
def test_invalid_order_actions_preserve_data(operation, start_state, detail, api_client, login_user, config, db, catalog):
    assert_ok(api_client.cart_add(catalog.s1, catalog.p2, 2))
    response = api_client.order_create()
    assert_ok(response)
    order = response.json()
    sub_id = order['sub_orders'][0]['sub_order_id']
    if start_state != 'pending':
        assert_ok(api_client.order_pay(order['order_id']))
    if start_state in ('shipped', 'completed'):
        seller = config['users']['seller1']
        assert_ok(api_client.login(seller['username'], seller['password']))
        assert_ok(api_client.sub_order_ship(sub_id))
        alice = config['users']['alice']
        assert_ok(api_client.login(alice['username'], alice['password']))
    if start_state == 'completed':
        assert_ok(api_client.sub_order_confirm(sub_id))
    if operation == 'ship':
        seller = config['users']['seller1']
        assert_ok(api_client.login(seller['username'], seller['password']))
    assert db.scalar('SELECT status FROM sub_orders WHERE sub_order_id=%s', (sub_id,)) == start_state
    before = _snapshot(db, catalog)
    response = api_client.sub_order_ship(sub_id) if operation == 'ship' else api_client.sub_order_confirm(sub_id)
    assert_error(response, 400, detail)
    assert _snapshot(db, catalog) == before

@allure.feature('拆单')
@allure.story('只收货一部分时父订单不能提前完成')
def test_parent_completes_only_after_all_suborders(api_client, login_user, config, db, catalog):
    for store, product in [(catalog.s1, catalog.p2), (catalog.s2, catalog.p5)]:
        assert_ok(api_client.cart_add(store, product, 1))
    response = api_client.order_create()
    assert_ok(response)
    order = response.json()
    assert len(order['sub_orders']) == 2
    subs = {s['store_id']: s['sub_order_id'] for s in order['sub_orders']}
    assert_ok(api_client.order_pay(order['order_id']))
    for store in (catalog.s1, catalog.s2):
        user = config['users'][f'seller{store}']
        assert_ok(api_client.login(user['username'], user['password']))
        assert_ok(api_client.sub_order_ship(subs[store]))
    user = config['users']['alice']
    assert_ok(api_client.login(user['username'], user['password']))
    assert_ok(api_client.sub_order_confirm(subs[catalog.s1]))
    assert db.scalar('SELECT status FROM sub_orders WHERE sub_order_id=%s', (subs[catalog.s1],)) == 'completed'
    assert db.scalar('SELECT status FROM sub_orders WHERE sub_order_id=%s', (subs[catalog.s2],)) == 'shipped'
    assert db.scalar('SELECT status FROM orders WHERE order_id=%s', (order['order_id'],)) == 'paid'
    assert_ok(api_client.sub_order_confirm(subs[catalog.s2]))
    assert db.scalar('SELECT status FROM orders WHERE order_id=%s', (order['order_id'],)) == 'completed'

@allure.feature('支付一致性')
@allure.story('跨店支付失败后所有子单取消且库存仅回补一次')
def test_cross_store_failure_preserves_amount_and_items(api_client, login_user, payment_mode, db, catalog):
    payment_mode('fail')
    prices = {p: db.scalar(f'SELECT price FROM store_product WHERE store_id={catalog.s2} AND product_id=%s', (p,)) for p in (catalog.p5, catalog.p6)}
    expected_total = prices[catalog.p5] * 2 + prices[catalog.p6]
    stocks = db.all(f'SELECT store_id, product_id, stock FROM store_product WHERE store_id IN ({catalog.s1},{catalog.s2}) ORDER BY store_id, product_id')
    keyboard_price = db.scalar(f'SELECT price FROM store_product WHERE store_id={catalog.s1} AND product_id={catalog.p2}')
    expected_by_store = {catalog.s1: keyboard_price, catalog.s2: expected_total}
    expected_total += keyboard_price
    for store, product, qty in [(catalog.s1, catalog.p2, 1), (catalog.s2, catalog.p5, 2), (catalog.s2, catalog.p6, 1)]:
        assert_ok(api_client.cart_add(store, product, qty))
    response = api_client.order_create()
    assert_ok(response)
    order = response.json()
    assert Decimal(str(order['total'])) == expected_total
    assert {s['store_id']: Decimal(str(s['total'])) for s in order['sub_orders']} == expected_by_store
    assert {s['store_id']: s['total'] for s in db.all('SELECT store_id, total FROM sub_orders WHERE parent_order_id=%s', (order['order_id'],))} == expected_by_store
    actual_items = db.all('SELECT so.store_id, oi.product_id, oi.price, oi.quantity FROM order_items oi JOIN sub_orders so ON oi.sub_order_id=so.sub_order_id WHERE so.parent_order_id=%s ORDER BY so.store_id, oi.product_id', (order['order_id'],))
    assert actual_items == [{'store_id': catalog.s1, 'product_id': catalog.p2, 'price': keyboard_price, 'quantity': 1}, {'store_id': catalog.s2, 'product_id': catalog.p5, 'price': prices[catalog.p5], 'quantity': 2}, {'store_id': catalog.s2, 'product_id': catalog.p6, 'price': prices[catalog.p6], 'quantity': 1}]
    items_before = db.all(f'SELECT * FROM order_items WHERE product_id IN ({catalog.product_ids_sql}) ORDER BY id')
    assert len(items_before) == 3
    response = api_client.order_pay(order['order_id'])
    assert_ok(response)
    assert response.json()['status'] == 'failed'
    assert db.one('SELECT status, total FROM orders WHERE order_id=%s', (order['order_id'],)) == {'status': 'canceled', 'total': expected_total}
    assert db.scalar("SELECT COUNT(*) FROM sub_orders WHERE parent_order_id=%s AND status='canceled'", (order['order_id'],)) == 2
    assert db.all(f'SELECT * FROM order_items WHERE product_id IN ({catalog.product_ids_sql}) ORDER BY id') == items_before
    assert db.all(f'SELECT store_id, product_id, stock FROM store_product WHERE store_id IN ({catalog.s1},{catalog.s2}) ORDER BY store_id, product_id') == stocks
    after_failure = _snapshot(db, catalog)
    assert_error(api_client.order_pay(order['order_id']), 400, '订单状态不允许支付')
    assert _snapshot(db, catalog) == after_failure
