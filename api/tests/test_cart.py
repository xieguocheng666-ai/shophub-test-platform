"""购物车测试：加购、查看、移除、边界校验、空购物车下单。"""
from pathlib import Path
import allure
import pytest
import yaml
from api.common.assert_util import assert_error, assert_ok
from api.common.db_util import assert_db_value
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

def _load_cart_cases():
    with open(ROOT_DIR / 'data' / 'cart.yaml', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    cases = []
    for group, items in data.items():
        for item in items:
            cases.append(pytest.param(item['store_role'], item['product_role'], item['quantity'], item['expect_code'], item.get('expect_detail'), id=item['id']))
    return cases

@allure.feature('购物车')
@allure.story('加购→查看→移除')
@allure.severity(allure.severity_level.CRITICAL)
def test_cart_add_get_remove(api_client, login_user, db, catalog):
    with allure.step('加购商品 1（数量 2）'):
        api_client.cart_add(catalog.s1, catalog.p1, 2)
        assert_db_value(db, 'SELECT quantity FROM carts WHERE username=%s AND store_id=%s AND product_id=%s', 2, (login_user['username'], catalog.s1, catalog.p1), '加购应写入购物车')
    with allure.step('查看购物车'):
        cart = api_client.cart_get().json()
        assert len(cart['items']) == 1
        assert cart['items'][0]['product_id'] == catalog.p1
        assert cart['items'][0]['quantity'] == 2
    with allure.step('移除商品'):
        cart = api_client.cart_remove(catalog.s1, catalog.p1).json()
        assert len(cart['items']) == 0
        assert_db_value(db, 'SELECT COUNT(*) AS n FROM carts WHERE username=%s AND store_id=%s AND product_id=%s', 0, (login_user['username'], catalog.s1, catalog.p1), '移除后购物车行应真删除')

@allure.feature('购物车')
@allure.story('加购边界校验')
@allure.severity(allure.severity_level.NORMAL)
@pytest.mark.parametrize('store_id,product_id,quantity,expect_code,expect_detail', _load_cart_cases())
def test_cart_add_boundary(store_id, product_id, quantity, expect_code, expect_detail, api_client, login_user, db, catalog):
    stocks_before = db.all(f'SELECT store_id, product_id, stock FROM store_product WHERE store_id IN ({catalog.s1},{catalog.s2}) ORDER BY store_id, product_id')
    store_id = getattr(catalog, store_id)
    product_id = getattr(catalog, product_id)
    resp = api_client.cart_add(store_id, product_id, quantity)
    assert_error(resp, expect_code, expect_detail)
    assert_db_value(db, 'SELECT COUNT(*) AS n FROM carts WHERE username=%s', 0, (login_user['username'],), '加购失败不应留下购物车数据')
    assert db.all(f'SELECT store_id, product_id, stock FROM store_product WHERE store_id IN ({catalog.s1},{catalog.s2}) ORDER BY store_id, product_id') == stocks_before

@allure.feature('购物车')
@allure.story('空购物车下单被拒')
@allure.severity(allure.severity_level.NORMAL)
def test_order_empty_cart_rejected(api_client, login_user, db, catalog):
    stocks_before = db.all(f'SELECT store_id, product_id, stock FROM store_product WHERE store_id IN ({catalog.s1},{catalog.s2}) ORDER BY store_id, product_id')
    resp = api_client.order_create()
    assert_error(resp, 400, '购物车为空')
    assert_db_value(db, 'SELECT COUNT(*) AS n FROM orders WHERE username=%s', 0, (login_user['username'],), '空购物车下单不应生成订单')
    assert db.scalar(f'SELECT COUNT(*) FROM sub_orders WHERE store_id IN ({catalog.s1},{catalog.s2})') == 0
    assert db.scalar(f'SELECT COUNT(*) FROM order_items WHERE product_id IN ({catalog.product_ids_sql})') == 0
    assert db.all(f'SELECT store_id, product_id, stock FROM store_product WHERE store_id IN ({catalog.s1},{catalog.s2}) ORDER BY store_id, product_id') == stocks_before

@allure.feature('购物车')
@allure.story('数量上下边界与下单库存')
@pytest.mark.parametrize('quantity', [1, 99, 100, 101], ids=['min', 'stock-minus-one', 'stock', 'stock-plus-one'])
def test_quantity_stock_boundary(quantity, api_client, login_user, db, catalog):
    """本例键盘库存100：加购不扣库存，成功下单才扣。"""
    assert db.scalar(f'SELECT stock FROM store_product WHERE store_id={catalog.s1} AND product_id={catalog.p2}') == 100
    resp = api_client.cart_add(catalog.s1, catalog.p2, quantity)
    if quantity > 100:
        assert_error(resp, 400, '库存不足')
        assert db.scalar(f"SELECT COUNT(*) FROM carts WHERE username IN ('{catalog.alice.username}','{catalog.bob.username}')") == 0
        assert db.scalar(f"SELECT COUNT(*) FROM orders WHERE username IN ('{catalog.alice.username}','{catalog.bob.username}')") == 0
    else:
        assert_ok(resp)
        assert resp.json()['items'][0]['quantity'] == quantity
        assert db.scalar('SELECT quantity FROM carts WHERE username=%s', (login_user['username'],)) == quantity
    assert db.scalar(f'SELECT stock FROM store_product WHERE store_id={catalog.s1} AND product_id={catalog.p2}') == 100
    if quantity <= 100:
        order = api_client.order_create()
        assert_ok(order)
        assert db.scalar(f'SELECT stock FROM store_product WHERE store_id={catalog.s1} AND product_id={catalog.p2}') == 100 - quantity
        assert db.scalar(f"SELECT COUNT(*) FROM carts WHERE username IN ('{catalog.alice.username}','{catalog.bob.username}')") == 0

@allure.feature('购物车')
@allure.story('累计加购边界，失败保留原购物车')
@pytest.mark.parametrize('extra,expected_code,expected_qty', [(39, 200, 99), (40, 200, 100), (41, 400, 60)])
def test_cart_accumulated_quantity(extra, expected_code, expected_qty, api_client, login_user, db, catalog):
    assert_ok(api_client.cart_add(catalog.s1, catalog.p2, 60))
    resp = api_client.cart_add(catalog.s1, catalog.p2, extra)
    if expected_code == 400:
        assert_error(resp, 400, '库存不足')
    else:
        assert_ok(resp)
    assert db.scalar(f'SELECT quantity FROM carts WHERE username=%s AND store_id={catalog.s1} AND product_id={catalog.p2}', (login_user['username'],)) == expected_qty
    assert db.scalar(f'SELECT stock FROM store_product WHERE store_id={catalog.s1} AND product_id={catalog.p2}') == 100
