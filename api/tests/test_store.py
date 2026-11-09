"""店铺越权矩阵 + 拆单与库存隔离测试。"""
import allure
from decimal import Decimal

from api.common.assert_util import assert_error, assert_ok
from api.common.db_util import assert_db_value
from api.tests.constants import STORE1, STORE2


# ---------- 越权矩阵（店铺维度） ----------
@allure.feature("越权矩阵")
@allure.story("垂直越权：买家注册店铺")
@allure.severity(allure.severity_level.NORMAL)
def test_buyer_cannot_create_store(api_client, login_user, db):
    stores_before = db.scalar("SELECT COUNT(*) AS n FROM stores")
    resp = api_client.create_store("alice 的小店")
    assert_error(resp, 403, "需要商家权限")
    assert_db_value(
        db, "SELECT COUNT(*) AS n FROM stores",
        stores_before, None, "越权开店不应写入 stores",
    )


@allure.feature("越权矩阵")
@allure.story("垂直越权：买家上架商品")
@allure.severity(allure.severity_level.NORMAL)
def test_buyer_cannot_add_product(api_client, login_user, db):
    products_before = db.scalar("SELECT COUNT(*) AS n FROM products")
    listed_before = db.scalar(
        "SELECT COUNT(*) AS n FROM store_product WHERE store_id=%s", (STORE1,)
    )
    resp = api_client.add_product(STORE1, "新商品", 99.0, 10)
    assert_error(resp, 403, "需要商家权限")
    assert_db_value(
        db, "SELECT COUNT(*) AS n FROM products",
        products_before, None, "越权上架不应新增商品 SPU",
    )
    assert_db_value(
        db, "SELECT COUNT(*) AS n FROM store_product WHERE store_id=%s",
        listed_before, (STORE1,), "越权上架不应新增店铺在售商品",
    )


@allure.feature("越权矩阵")
@allure.story("水平越权：卖家改他人店铺库存")
@allure.severity(allure.severity_level.NORMAL)
def test_seller_cannot_operate_others_store(api_client, config, db):
    seller1 = config["users"]["seller1"]
    stock_before = db.scalar(
        "SELECT stock FROM store_product WHERE store_id=%s AND product_id=1", (STORE2,)
    )
    with allure.step("seller1 登录并尝试改 store2（seller2 的店）的库存"):
        api_client.login(seller1["username"], seller1["password"])
        resp = api_client.update_stock(STORE2, 1, 100)
    with allure.step("断言越权被拒（403）"):
        assert_error(resp, 403, "无权操作他人店铺")
    with allure.step("确认他人店铺库存没有被改动"):
        assert_db_value(
            db, "SELECT stock FROM store_product WHERE store_id=%s AND product_id=1",
            stock_before, (STORE2,), "越权改库存不应生效",
        )


@allure.feature("越权矩阵")
@allure.story("水平越权：卖家发他人店铺子订单")
@allure.severity(allure.severity_level.NORMAL)
def test_seller_cannot_ship_others_sub_order(api_client, config, db):
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
        assert_error(resp, 403, "无权操作他人店铺")
    with allure.step("确认他人的子订单没有被发货"):
        assert_db_value(
            db, "SELECT status FROM sub_orders WHERE sub_order_id=%s",
            "paid", (sub_order_id,), "越权发货不应改变子订单状态",
        )


# ---------- 拆单与库存隔离 ----------
@allure.feature("拆单")
@allure.story("跨店铺下单拆成多个子订单")
@allure.severity(allure.severity_level.CRITICAL)
def test_order_split_by_store(api_client, login_user, db):
    price1 = db.scalar("SELECT price FROM store_product WHERE store_id=%s AND product_id=1", (STORE1,))
    price5 = db.scalar("SELECT price FROM store_product WHERE store_id=%s AND product_id=5", (STORE2,))
    expected_by_store = {STORE1: price1, STORE2: price5}
    expected_total = price1 + price5
    stock1_before = db.scalar(
        "SELECT stock FROM store_product WHERE store_id=%s AND product_id=1", (STORE1,)
    )
    stock5_before = db.scalar(
        "SELECT stock FROM store_product WHERE store_id=%s AND product_id=5", (STORE2,)
    )
    with allure.step("购物车同时加购 store1 和 store2 的商品"):
        api_client.cart_add(STORE1, 1, 1)
        api_client.cart_add(STORE2, 5, 1)
    with allure.step("下单"):
        order_resp = api_client.order_create()
        assert_ok(order_resp)
        data = order_resp.json()
        order_id = data["order_id"]
    with allure.step("断言拆成 2 个子订单，各属一个店铺"):
        sub_orders = data["sub_orders"]
        assert len(sub_orders) == 2
        assert {so["store_id"] for so in sub_orders} == {STORE1, STORE2}
    with allure.step("断言父订单总额 = 子订单之和"):
        assert data["total"] == sum(so["total"] for so in sub_orders)
        assert Decimal(str(data["total"])) == expected_total
        assert {so["store_id"]: Decimal(str(so["total"])) for so in sub_orders} == expected_by_store
        assert db.scalar("SELECT total FROM orders WHERE order_id=%s", (order_id,)) == expected_total
        assert {r["store_id"]: r["total"] for r in db.all("SELECT store_id, total FROM sub_orders WHERE parent_order_id=%s", (order_id,))} == expected_by_store

    with allure.step("直连数据库确认拆单结构正确落库"):
        rows = db.all(
            "SELECT store_id FROM sub_orders WHERE parent_order_id=%s ORDER BY store_id",
            (order_id,),
        )
        assert [r["store_id"] for r in rows] == [STORE1, STORE2], "子订单应按店铺拆成两行"
        rows = db.all(
            "SELECT so.store_id, oi.product_id, oi.quantity FROM order_items oi "
            "JOIN sub_orders so ON oi.sub_order_id=so.sub_order_id "
            "WHERE so.parent_order_id=%s ORDER BY so.store_id",
            (order_id,),
        )
        assert [(r["store_id"], r["product_id"], r["quantity"]) for r in rows] == [
            (STORE1, 1, 1),
            (STORE2, 5, 1),
        ], "明细必须挂到对应店铺的子订单下"
        item_total = db.scalar(
            "SELECT SUM(oi.price*oi.quantity) FROM order_items oi "
            "JOIN sub_orders so ON oi.sub_order_id=so.sub_order_id "
            "WHERE so.parent_order_id=%s",
            (order_id,),
        )
        assert item_total == expected_total, (
            "父订单总额应等于明细金额之和"
        )
        assert_db_value(
            db, "SELECT COUNT(*) AS n FROM carts WHERE username=%s",
            0, (login_user["username"],), "下单后购物车应清空",
        )
        assert_db_value(
            db, "SELECT stock FROM store_product WHERE store_id=%s AND product_id=1",
            stock1_before - 1, (STORE1,), "store1 库存扣减",
        )
        assert_db_value(
            db, "SELECT stock FROM store_product WHERE store_id=%s AND product_id=5",
            stock5_before - 1, (STORE2,), "store2 库存扣减",
        )


@allure.feature("拆单")
@allure.story("同商品多店铺库存隔离")
@allure.severity(allure.severity_level.CRITICAL)
def test_same_product_multi_store_stock_isolation(api_client, login_user, db):
    with allure.step("记录商品 1 在两家店的库存"):
        stock1_before = api_client.store_product(STORE1, 1).json()["stock"]
        stock2_before = api_client.store_product(STORE2, 1).json()["stock"]
    with allure.step("从 store1 买 1 件商品 1"):
        api_client.cart_add(STORE1, 1, 1)
        api_client.order_create()
    with allure.step("断言 store1 库存 -1，store2 库存不变"):
        assert api_client.store_product(STORE1, 1).json()["stock"] == stock1_before - 1
        assert api_client.store_product(STORE2, 1).json()["stock"] == stock2_before
        assert_db_value(
            db, "SELECT stock FROM store_product WHERE store_id=%s AND product_id=1",
            stock1_before - 1, (STORE1,), "store1 库存扣减",
        )
        assert_db_value(
            db, "SELECT stock FROM store_product WHERE store_id=%s AND product_id=1",
            stock2_before, (STORE2,), "store2 库存不应被连带修改",
        )


@allure.feature("拆单")
@allure.story("跨店铺支付失败，两店库存均回补")
@allure.severity(allure.severity_level.CRITICAL)
def test_cross_store_pay_fail_restores_all(api_client, login_user, payment_mode, db):
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
        assert_db_value(
            db, "SELECT status FROM orders WHERE order_id=%s",
            "canceled", (order_id,), "跨店支付失败后父订单应取消",
        )
        assert_db_value(
            db, "SELECT stock FROM store_product WHERE store_id=%s AND product_id=1",
            stock1_before, (STORE1,), "store1 库存回补",
        )
        assert_db_value(
            db, "SELECT stock FROM store_product WHERE store_id=%s AND product_id=5",
            stock5_before, (STORE2,), "store2 库存回补",
        )
