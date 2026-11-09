"""独立买家竞争最后一件库存；并发收货聚合状态。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import allure
import pytest

from api.api_client import ApiClient
from api.common.assert_util import assert_ok, assert_error
from api.tests.constants import STORE1, STORE2


@allure.feature("并发防超卖")
@allure.story("两个独立买家抢最后一件")
@pytest.mark.parametrize("product_id", [3, 4], ids=["mouse", "monitor"])
def test_concurrent_order_no_oversell(product_id, base_url, config, db):
    seller = ApiClient(base_url, retry=0)
    user = config["users"]["seller1"]
    assert_ok(seller.login(user["username"], user["password"]))
    assert_ok(seller.update_stock(STORE1, product_id, 1))
    clients = []
    for name in ("alice", "bob"):
        client = ApiClient(base_url, retry=0)
        user = config["users"][name]
        assert_ok(client.login(user["username"], user["password"]))
        assert_ok(client.cart_add(STORE1, product_id, 1))
        clients.append(client)
    barrier = Barrier(2, timeout=10)

    def place_order(client):
        barrier.wait()
        return client.order_create()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(place_order, clients))
        assert sorted(r.status_code for r in results) == [200, 400], [r.text for r in results]
        winner = next(r for r in results if r.status_code == 200)
        loser_index = next(i for i, r in enumerate(results) if r.status_code == 400)
        assert_error(results[loser_index], 400, "库存不足")
        assert db.scalar("SELECT stock FROM store_product WHERE store_id=%s AND product_id=%s", (STORE1, product_id)) == 0
        assert db.scalar("SELECT COUNT(*) FROM orders") == 1
        assert db.scalar("SELECT COUNT(*) FROM sub_orders") == 1
        assert db.all("SELECT product_id, quantity FROM order_items") == [{"product_id": product_id, "quantity": 1}]
        winner_name = ("alice", "bob")[1 - loser_index]
        loser_name = ("alice", "bob")[loser_index]
        order = db.one("SELECT username, status FROM orders WHERE order_id=%s", (winner.json()["order_id"],))
        assert order == {"username": winner_name, "status": "pending"}
        assert db.all("SELECT username, quantity FROM carts") == [{"username": loser_name, "quantity": 1}]
    finally:
        seller.session.close()
        for client in clients:
            client.session.close()


@allure.feature("并发确认收货")
@allure.story("并发收货全部子单，父单最终完成")
def test_concurrent_confirm_parent_completed(base_url, config, db):
    buyer = ApiClient(base_url, retry=0)
    seller = ApiClient(base_url, retry=0)
    try:
        user = config["users"]["alice"]
        assert_ok(buyer.login(user["username"], user["password"]))
        for store, product in [(STORE1, 1), (STORE2, 5)]:
            assert_ok(buyer.cart_add(store, product, 1))
        response = buyer.order_create()
        assert_ok(response)
        order = response.json()
        assert len(order["sub_orders"]) == 2
        assert_ok(buyer.order_pay(order["order_id"]))
        for sub in order["sub_orders"]:
            user = config["users"][f"seller{sub['store_id']}"]
            assert_ok(seller.login(user["username"], user["password"]))
            assert_ok(seller.sub_order_ship(sub["sub_order_id"]))
        # 两条线程各持独立 Session，避免客户端共享状态影响服务端并发结论。
        clients = [ApiClient(base_url, retry=0) for _ in range(2)]
        for client in clients:
            client.token = buyer.token
        barrier = Barrier(2, timeout=10)

        def confirm(pair):
            client, sub = pair
            barrier.wait()
            return client.sub_order_confirm(sub["sub_order_id"])

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(confirm, zip(clients, order["sub_orders"])))
            for response in responses:
                assert_ok(response)
                assert response.json()["status"] == "completed"
        finally:
            for client in clients:
                client.session.close()
        assert db.scalar("SELECT status FROM orders WHERE order_id=%s", (order["order_id"],)) == "completed"
        rows = db.all("SELECT status FROM sub_orders WHERE parent_order_id=%s", (order["order_id"],))
        assert len(rows) == 2
        assert all(row["status"] == "completed" for row in rows)
    finally:
        buyer.session.close()
        seller.session.close()
