"""ApiClient：对 requests 的封装，接口测试的统一入口。

核心对象：requests.Session。ApiClient 内部持有它，并维护登录后的 JWT，
受保护接口自动带上 Authorization: Bearer <token>。
"""
import logging
import time
import allure
import requests

logger = logging.getLogger(__name__)


class ApiClient:
    """被测系统的统一 HTTP 客户端。

    base_url: 被测系统地址（从 config/config.yaml 读入，不写死）
    timeout:  单次请求超时秒数，防止测试卡死
    retry:    网络层失败时重试次数（仅网络异常才重试，业务 4xx/5xx 不重试）
    """

    def __init__(self, base_url: str, timeout: int = 5, retry: int = 2):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retry = retry
        self.session = requests.Session()
        self.token = None  # 登录后持有的 JWT

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", self.timeout)
        for attempt in range(self.retry + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
                logger.info("[%s] %s -> %s", method.upper(), url, resp.status_code)
                return resp
            except requests.RequestException as exc:
                if attempt == self.retry:
                    raise
                logger.warning("请求失败，第 %d 次重试: %s", attempt + 1, exc)
                time.sleep(1)

    def _headers(self):
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    # ---------- 业务方法 ----------
    def login(self, username: str, password: str):
        resp = self._request(
            "POST", "/login", json={"username": username, "password": password}
        )
        if resp.status_code == 200:
            self.token = resp.json()["token"]
        return resp

    def health(self):
        return self._request("GET", "/health")

    def me(self):
        return self._request("GET", "/me", headers=self._headers())

    def products(self, keyword: str = ""):
        return self._request("GET", "/products", params={"keyword": keyword})

    def store_product(self, store_id: int, product_id: int):
        return self._request("GET", f"/stores/{store_id}/products/{product_id}")

    def cart_add(self, store_id: int, product_id: int, quantity: int = 1):
        return self._request(
            "POST", "/cart", headers=self._headers(),
            json={"store_id": store_id, "product_id": product_id, "quantity": quantity},
        )

    def cart_get(self):
        return self._request("GET", "/cart", headers=self._headers())

    def cart_remove(self, store_id: int, product_id: int):
        return self._request("DELETE", f"/cart/{store_id}/{product_id}", headers=self._headers())

    @allure.step("创建订单")
    def order_create(self):
        return self._request("POST", "/order", headers=self._headers())

    @allure.step("支付订单")
    def order_pay(self, order_id: int):
        return self._request("POST", f"/order/{order_id}/pay", headers=self._headers())

    def sub_order_ship(self, sub_order_id: int):
        return self._request("POST", f"/sub-orders/{sub_order_id}/ship", headers=self._headers())

    def sub_order_confirm(self, sub_order_id: int):
        return self._request("POST", f"/sub-orders/{sub_order_id}/confirm", headers=self._headers())

    def orders(self):
        return self._request("GET", "/orders", headers=self._headers())

    # ---------- 店铺管理（seller） ----------
    def create_store(self, store_name: str):
        return self._request(
            "POST", "/stores", headers=self._headers(), json={"store_name": store_name}
        )

    def list_my_stores(self):
        return self._request("GET", "/stores", headers=self._headers())

    def add_product(self, store_id: int, name: str, price: float, stock: int):
        return self._request(
            "POST", f"/stores/{store_id}/products", headers=self._headers(),
            json={"name": name, "price": price, "stock": stock},
        )

    def update_stock(self, store_id: int, product_id: int, stock: int):
        return self._request(
            "PATCH", f"/stores/{store_id}/products/{product_id}/stock",
            headers=self._headers(), json={"stock": stock},
        )

    def set_payment_mode(self, mode: str):
        return self._request("POST", "/debug/payment-mode", json={"mode": mode})

    def cancel_expired(self, seconds: int = 0):
        return self._request("POST", f"/debug/cancel-expired?seconds={seconds}")

    def reset(self):
        return self._request("POST", "/debug/reset")
