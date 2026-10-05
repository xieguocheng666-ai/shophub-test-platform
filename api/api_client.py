"""ApiClient：接口测试的统一入口，按业务模块拆成 mixin 组合而成。

核心对象：requests.Session。ApiClient 持有它并维护登录后的 JWT，
受保护接口自动带上 Authorization: Bearer <token>。
业务方法按模块拆分在 api/clients/ 下（auth/product/cart/order/store/debug），
ApiClient 通过多重继承聚合它们，测试里仍用统一的 api_client.xxx() 调用。
"""
import logging
import time

import requests

from api.clients.auth import AuthMixin
from api.clients.cart import CartMixin
from api.clients.debug import DebugMixin
from api.clients.order import OrderMixin
from api.clients.product import ProductMixin
from api.clients.store import StoreMixin

logger = logging.getLogger(__name__)


class ApiClient(AuthMixin, ProductMixin, CartMixin, OrderMixin, StoreMixin, DebugMixin):
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
