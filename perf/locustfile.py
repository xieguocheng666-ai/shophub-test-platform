"""ShopHub 压测脚本：登录 / 搜索 / 下单三接口。

用法（在项目根目录）：
    # 搜索场景
    SCENARIO=search locust -f perf/locustfile.py --headless -u 100 -r 50 -t 60s --csv perf/results/base_search_100
    # 下单场景（分散商品，测整体吞吐）
    SCENARIO=order ORDER_MODE=spread locust -f perf/locustfile.py --headless -u 100 -r 50 -t 60s --csv perf/results/base_order_100
    # 下单场景（抢同一商品，测行锁竞争吞吐上限）
    SCENARIO=order ORDER_MODE=hot locust -f perf/locustfile.py --headless -u 100 -r 50 -t 60s --csv perf/results/hot_order_100

环境变量：
    SCENARIO       login | search | order（默认 order）
    ORDER_MODE     hot（抢同一商品，测行锁）| spread（分散商品，默认）
    BASE_URL       被测地址（默认 http://127.0.0.1:8000）
    PERF_USER_COUNT 账号池大小（默认 1000）
"""
import itertools
import os
import random
import time

from locust import HttpUser, constant, task

SCENARIO = os.getenv("SCENARIO", "order")
ORDER_MODE = os.getenv("ORDER_MODE", "spread")
PERF_USER_COUNT = int(os.getenv("PERF_USER_COUNT", "1000"))

PASSWORD = "perf123"

# 下单可选商品（store_id, product_id）。spread 模式按账号序号取模分散
ORDER_PRODUCTS = [
    (2, 5), (2, 6), (2, 7), (2, 8),
    (1, 1), (1, 2), (1, 3), (1, 4),
]
HOT_PRODUCT = (2, 5)  # 行锁竞争：所有人抢同一行 store_product(2,5)

KEYWORDS = ["", "键盘", "鼠标", "耳机", "笔记本", "U盘"]

_user_index = itertools.count()


def _auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


class ShopUser(HttpUser):
    host = os.getenv("BASE_URL", "http://127.0.0.1:8000")
    wait_time = constant(0)  # 压满，无思考时间

    def on_start(self):
        self.user_idx = next(_user_index) % PERF_USER_COUNT
        self.username = f"perfuser{self.user_idx}"
        self.token = None
        self.store_id = self.product_id = None

        if SCENARIO == "order":
            self._ensure_login_and_cart()

    def _ensure_login_and_cart(self):
        """登录（失败重试）+ 加购，确保下单前购物车有货、token 有效。"""
        for _ in range(3):
            self.token = self._login()
            if self.token:
                break
            time.sleep(0.5)
        if not self.token:
            return
        self._pick_product()
        self._add_cart()

    def _login(self):
        r = self.client.post(
            "/login",
            json={"username": self.username, "password": PASSWORD},
            name="login",
        )
        return r.json().get("token") if r.status_code == 200 else None

    def _pick_product(self):
        if ORDER_MODE == "hot":
            self.store_id, self.product_id = HOT_PRODUCT
        else:
            self.store_id, self.product_id = ORDER_PRODUCTS[
                self.user_idx % len(ORDER_PRODUCTS)
            ]

    def _add_cart(self):
        self.client.post(
            "/cart",
            json={"store_id": self.store_id, "product_id": self.product_id, "quantity": 1},
            headers=_auth_headers(self.token),
            name="cart_add",
        )

    @task
    def do_task(self):
        if SCENARIO == "login":
            # 每次随机挑一个压测账号登录，模拟不同用户
            username = f"perfuser{random.randint(0, PERF_USER_COUNT - 1)}"
            self.client.post(
                "/login",
                json={"username": username, "password": PASSWORD},
                name="login",
            )
        elif SCENARIO == "search":
            kw = random.choice(KEYWORDS)
            self.client.get(f"/products?keyword={kw}", name="search")
        else:  # order
            if self.token is None:
                # 登录此前失败，补一次，避免 401 死循环
                self._ensure_login_and_cart()
                if self.token is None:
                    return
            r = self.client.post(
                "/order",
                headers=_auth_headers(self.token),
                name="order",
            )
            if r.status_code == 200:
                # 下单成功会清空购物车，补购为下一单做准备
                self._add_cart()
