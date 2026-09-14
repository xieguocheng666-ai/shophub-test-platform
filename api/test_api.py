"""被测系统接口测试：基础冒烟 + 鉴权 / 状态机 / 支付异常 / 并发 / 全链路 / 购物车 / 越权矩阵 / 拆单 八类业务场景。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import allure
import jwt as pyjwt
import pytest
import requests
import yaml

from api.api_client import ApiClient

ROOT_DIR = Path(__file__).resolve().parent.parent

# 种子数据里的店铺 id（server/db.py SEED_STORES）
STORE1, STORE2 = 1, 2


def _load_login_cases():
    with open(ROOT_DIR / "data" / "users.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cases = []
    for group, items in data.items():
        for item in items:
            cases.append(
                pytest.param(
                    item["username"], item["password"], item["expect_code"],
                    id=item["id"],
                )
            )
    return cases


def _load_search_cases():
    with open(ROOT_DIR / "data" / "search.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cases = []
    for group, items in data.items():
        for item in items:
            cases.append(
                pytest.param(
                    item["keyword"], item["expect_count"],
                    id=item["id"],
                )
            )
    return cases


# ---------- 基础 ----------
@allure.severity(allure.severity_level.BLOCKER)
def test_health(api_client):
    resp = api_client.health()
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("username,password,expect_code", _load_login_cases())
def test_login(username, password, expect_code, api_client):
    resp = api_client.login(username, password)
    assert resp.status_code == expect_code


@allure.severity(allure.severity_level.NORMAL)
@pytest.mark.parametrize("keyword,expect_count", _load_search_cases())
def test_search_products(keyword, expect_count, api_client):
    resp = api_client.products(keyword)
    items = resp.json()["items"]
    assert len(items) == expect_count
    if keyword:
        assert all(keyword.lower() in item["name"].lower() for item in items)


# ---------- 组 1：鉴权 ----------
@allure.feature("鉴权")
@allure.story("缺失 token")
@allure.severity(allure.severity_level.NORMAL)
def test_me_without_token(base_url):
    resp = requests.get(f"{base_url}/me")
    assert resp.status_code == 401


@allure.feature("鉴权")
@allure.story("伪造 token")
@allure.severity(allure.severity_level.NORMAL)
def test_me_with_forged_token(base_url):
    resp = requests.get(f"{base_url}/me", headers={"Authorization": "Bearer forged.token"})
    assert resp.status_code == 401


@allure.feature("鉴权")
@allure.story("错误签名")
@allure.severity(allure.severity_level.NORMAL)
def test_me_with_wrong_signature(base_url):
    token = pyjwt.encode({"username": "alice"}, "wrong-secret-key-for-testing-only", algorithm="HS256")
    resp = requests.get(f"{base_url}/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@allure.feature("鉴权")
@allure.story("过期 token")
@allure.severity(allure.severity_level.NORMAL)
def test_me_with_expired_token(base_url, config):
    token = pyjwt.encode(
        {"username": "alice", "exp": datetime.utcnow() - timedelta(seconds=10)},
        config["secret_key"], algorithm="HS256",
    )
    resp = requests.get(f"{base_url}/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


@allure.feature("鉴权")
@allure.story("合法 token")
@allure.severity(allure.severity_level.NORMAL)
def test_me_with_valid_token(api_client, login_user):
    resp = api_client.me()
    assert resp.status_code == 200
    assert resp.json()["username"] == "alice"
    assert resp.json()["nickname"] == "爱丽丝"


# ---------- 组 2：状态机 ----------
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
        assert order_resp.status_code == 200
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
        assert resp.status_code == 400


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
        assert resp.status_code == 404


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
        assert resp.status_code == 403


# ---------- 组 3：支付异常 ----------
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
        assert resp.status_code == 200
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
        assert resp.status_code == 400


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


# ---------- 组 4：并发防超卖 ----------
@allure.feature("并发防超卖")
@allure.story("三号商品防超卖测试")
@allure.severity(allure.severity_level.CRITICAL)
def test_concurrent_order_no_oversell(base_url, config):
    with allure.step("bob 登录并查询商品 3 库存"):
        bob = config["users"]["bob"]
        login_resp = requests.post(
            f"{base_url}/login",
            json={"username": bob["username"], "password": bob["password"]},
        )
        token = login_resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        store_id, product_id = STORE1, 3
        stock = requests.get(
            f"{base_url}/stores/{store_id}/products/{product_id}"
        ).json()["stock"]

    def place_order(_):
        requests.post(
            f"{base_url}/cart", headers=headers,
            json={"store_id": store_id, "product_id": product_id, "quantity": 1},
        )
        return requests.post(f"{base_url}/order", headers=headers).status_code

    with allure.step("20 线程并发下单（超出库存）"):
        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(place_order, range(stock + 30)))

    with allure.step("断言无超卖"):
        success = sum(1 for c in results if c == 200)
        final_stock = requests.get(
            f"{base_url}/stores/{store_id}/products/{product_id}"
        ).json()["stock"]
        assert success <= stock
        assert final_stock >= 0


@allure.feature("并发防超卖")
@allure.story("四号商品防超卖测试")
@allure.severity(allure.severity_level.CRITICAL)
def test_concurrent_order_no_oversell_another(base_url, config):
    with allure.step("bob 登录并查询商品 4 库存"):
        bob = config["users"]["bob"]
        login_resp = requests.post(
            f"{base_url}/login",
            json={"username": bob["username"], "password": bob["password"]},
        )
        token = login_resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        store_id, product_id = STORE1, 4
        stock = requests.get(
            f"{base_url}/stores/{store_id}/products/{product_id}"
        ).json()["stock"]

    def place_order(_):
        requests.post(
            f"{base_url}/cart", headers=headers,
            json={"store_id": store_id, "product_id": product_id, "quantity": 1},
        )
        return requests.post(f"{base_url}/order", headers=headers).status_code

    with allure.step("20 线程并发下单（超出库存）"):
        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(place_order, range(stock + 20)))

    with allure.step("断言无超卖"):
        success = sum(1 for c in results if c == 200)
        final_stock = requests.get(
            f"{base_url}/stores/{store_id}/products/{product_id}"
        ).json()["stock"]
        assert success <= stock
        assert final_stock >= 0


@allure.feature("并发确认收货")
@allure.story("并发确认两个子订单，父订单正确流转为 completed")
@allure.severity(allure.severity_level.CRITICAL)
def test_concurrent_confirm_parent_completed(base_url, config):
    alice = config["users"]["alice"]
    seller1 = config["users"]["seller1"]
    seller2 = config["users"]["seller2"]

    def login(user):
        resp = requests.post(
            f"{base_url}/login",
            json={"username": user["username"], "password": user["password"]},
        )
        return {"Authorization": f"Bearer {resp.json()['token']}"}

    with allure.step("alice 跨店铺下单（拆 2 子订单）并支付"):
        alice_headers = login(alice)
        requests.post(
            f"{base_url}/cart", headers=alice_headers,
            json={"store_id": STORE1, "product_id": 1, "quantity": 1},
        )
        requests.post(
            f"{base_url}/cart", headers=alice_headers,
            json={"store_id": STORE2, "product_id": 5, "quantity": 1},
        )
        order_resp = requests.post(f"{base_url}/order", headers=alice_headers)
        order_id = order_resp.json()["order_id"]
        sub_orders = order_resp.json()["sub_orders"]
        assert len(sub_orders) == 2
        sub1 = next(so["sub_order_id"] for so in sub_orders if so["store_id"] == STORE1)
        sub2 = next(so["sub_order_id"] for so in sub_orders if so["store_id"] == STORE2)
        requests.post(f"{base_url}/order/{order_id}/pay", headers=alice_headers)

    with allure.step("两个店铺分别发货"):
        ship1 = requests.post(f"{base_url}/sub-orders/{sub1}/ship", headers=login(seller1))
        ship2 = requests.post(f"{base_url}/sub-orders/{sub2}/ship", headers=login(seller2))
        assert ship1.status_code == 200 and ship2.status_code == 200

    def confirm(sub_id):
        return requests.post(
            f"{base_url}/sub-orders/{sub_id}/confirm", headers=alice_headers
        ).status_code

    with allure.step("并发确认两个子订单"):
        with ThreadPoolExecutor(max_workers=2) as ex:
            results = list(ex.map(confirm, [sub1, sub2]))

    with allure.step("断言父订单最终状态为 completed"):
        assert all(code == 200 for code in results)
        detail = requests.get(f"{base_url}/order/{order_id}", headers=alice_headers).json()
        assert detail["status"] == "completed"


# ---------- 组 5：全链路 ----------
@allure.feature("全链路")
@allure.story("搜索→加购→下单→支付→发货→收货")
@allure.severity(allure.severity_level.CRITICAL)
def test_full_ecommerce_flow(api_client, login_user, config):
    seller1 = config["users"]["seller1"]
    alice = config["users"]["alice"]

    with allure.step("搜索商品"):
        resp = api_client.products("罗技")
        assert resp.status_code == 200
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


# ---------- 组 6：购物车 ----------
@allure.feature("购物车")
@allure.story("加购→查看→移除")
@allure.severity(allure.severity_level.CRITICAL)
def test_cart_add_get_remove(api_client, login_user):
    with allure.step("加购商品 1（数量 2）"):
        api_client.cart_add(STORE1, 1, 2)
    with allure.step("查看购物车"):
        cart = api_client.cart_get().json()
        assert len(cart["items"]) == 1
        assert cart["items"][0]["product_id"] == 1
        assert cart["items"][0]["quantity"] == 2
    with allure.step("移除商品"):
        cart = api_client.cart_remove(STORE1, 1).json()
        assert len(cart["items"]) == 0


@allure.feature("购物车")
@allure.story("库存不足被拒")
@allure.severity(allure.severity_level.NORMAL)
def test_cart_add_insufficient_stock(api_client, login_user):
    # 商品 4 库存 30，999 远超库存，必触发 400
    resp = api_client.cart_add(STORE1, 4, 999)
    assert resp.status_code == 400


@allure.feature("购物车")
@allure.story("数量非法被拒")
@allure.severity(allure.severity_level.NORMAL)
def test_cart_add_invalid_quantity(api_client, login_user):
    resp = api_client.cart_add(STORE1, 1, 0)
    assert resp.status_code == 400


@allure.feature("购物车")
@allure.story("商品不存在")
@allure.severity(allure.severity_level.NORMAL)
def test_cart_add_nonexistent_product(api_client, login_user):
    resp = api_client.cart_add(STORE1, 9999, 1)
    assert resp.status_code == 404


@allure.feature("购物车")
@allure.story("空购物车下单被拒")
@allure.severity(allure.severity_level.NORMAL)
def test_order_empty_cart_rejected(api_client, login_user):
    resp = api_client.order_create()
    assert resp.status_code == 400


# ---------- 组 7：越权矩阵（店铺维度） ----------
@allure.feature("越权矩阵")
@allure.story("垂直越权：买家注册店铺")
@allure.severity(allure.severity_level.NORMAL)
def test_buyer_cannot_create_store(api_client, login_user):
    resp = api_client.create_store("alice 的小店")
    assert resp.status_code == 403


@allure.feature("越权矩阵")
@allure.story("垂直越权：买家上架商品")
@allure.severity(allure.severity_level.NORMAL)
def test_buyer_cannot_add_product(api_client, login_user):
    resp = api_client.add_product(STORE1, "新商品", 99.0, 10)
    assert resp.status_code == 403


@allure.feature("越权矩阵")
@allure.story("水平越权：卖家改他人店铺库存")
@allure.severity(allure.severity_level.NORMAL)
def test_seller_cannot_operate_others_store(api_client, config):
    seller1 = config["users"]["seller1"]
    with allure.step("seller1 登录并尝试改 store2（seller2 的店）的库存"):
        api_client.login(seller1["username"], seller1["password"])
        resp = api_client.update_stock(STORE2, 1, 100)
    with allure.step("断言越权被拒（403）"):
        assert resp.status_code == 403


@allure.feature("越权矩阵")
@allure.story("水平越权：卖家发他人店铺子订单")
@allure.severity(allure.severity_level.NORMAL)
def test_seller_cannot_ship_others_sub_order(api_client, config):
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
        assert resp.status_code == 403


# ---------- 组 8：拆单与库存隔离 ----------
@allure.feature("拆单")
@allure.story("跨店铺下单拆成多个子订单")
@allure.severity(allure.severity_level.CRITICAL)
def test_order_split_by_store(api_client, login_user):
    with allure.step("购物车同时加购 store1 和 store2 的商品"):
        api_client.cart_add(STORE1, 1, 1)
        api_client.cart_add(STORE2, 5, 1)
    with allure.step("下单"):
        order_resp = api_client.order_create()
        assert order_resp.status_code == 200
        data = order_resp.json()
    with allure.step("断言拆成 2 个子订单，各属一个店铺"):
        sub_orders = data["sub_orders"]
        assert len(sub_orders) == 2
        assert {so["store_id"] for so in sub_orders} == {STORE1, STORE2}
    with allure.step("断言父订单总额 = 子订单之和"):
        assert data["total"] == sum(so["total"] for so in sub_orders)


@allure.feature("拆单")
@allure.story("同商品多店铺库存隔离")
@allure.severity(allure.severity_level.CRITICAL)
def test_same_product_multi_store_stock_isolation(api_client, login_user):
    with allure.step("记录商品 1 在两家店的库存"):
        stock1_before = api_client.store_product(STORE1, 1).json()["stock"]
        stock2_before = api_client.store_product(STORE2, 1).json()["stock"]
    with allure.step("从 store1 买 1 件商品 1"):
        api_client.cart_add(STORE1, 1, 1)
        api_client.order_create()
    with allure.step("断言 store1 库存 -1，store2 库存不变"):
        assert api_client.store_product(STORE1, 1).json()["stock"] == stock1_before - 1
        assert api_client.store_product(STORE2, 1).json()["stock"] == stock2_before


@allure.feature("拆单")
@allure.story("跨店铺支付失败，两店库存均回补")
@allure.severity(allure.severity_level.CRITICAL)
def test_cross_store_pay_fail_restores_all(api_client, login_user, payment_mode):
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


# ---------- 组 9：异步任务（超时未支付自动关单） ----------
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
        assert resp.status_code == 200
        assert resp.json()["cancelled"] == 1
    with allure.step("断言订单已取消、库存已回补"):
        assert api_client.orders().json()["orders"][-1]["status"] == "canceled"
        assert api_client.store_product(store_id, product_id).json()["stock"] == stock_before
