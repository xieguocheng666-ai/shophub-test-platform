"""并发测试：并发下单防超卖、并发确认收货父订单状态流转。"""
from concurrent.futures import ThreadPoolExecutor

import allure
import requests

from api.common.assert_util import assert_status
from api.tests.constants import STORE1, STORE2


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
        assert_status(ship1, 200)
        assert_status(ship2, 200)

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
