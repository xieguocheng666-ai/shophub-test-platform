"""ApiClient：接口测试的统一入口，按业务模块拆成 mixin 组合而成。

核心对象：requests.Session。ApiClient 持有它并维护登录后的 JWT，
受保护接口自动带上 Authorization: Bearer <token>。
业务方法按模块拆分在 api/clients/ 下（auth/product/cart/order/store/debug），
ApiClient 通过多重继承聚合它们，测试里仍用统一的 api_client.xxx() 调用。
"""
import logging
import time
from uuid import uuid4

import requests

from test_support import diagnostics

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
    retry:    GET/HEAD 连接异常或超时的额外重试次数；写请求不自动重放，HTTP 错误不重试
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
        retries = self.retry if method.upper() in {"GET", "HEAD"} else 0
        # 捕获共享用例对象，线程中的请求也能归属当前顺序用例。
        try:
            case = diagnostics.get_case()
        except Exception:
            case = None
        for attempt in range(retries + 1):
            request_id = uuid4().hex
            headers = requests.structures.CaseInsensitiveDict(kwargs.get("headers") or {})
            headers["X-Request-ID"] = request_id
            kwargs["headers"] = headers
            started = time.monotonic()
            resp = None
            error = None
            try:
                resp = self.session.request(method, url, **kwargs)
                try:
                    logger.info("request_id=%s method=%s status=%s elapsed_ms=%.3f",
                                request_id, method.upper(), resp.status_code,
                                (time.monotonic() - started) * 1000)
                except Exception:
                    pass
                return resp
            except Exception as exc:
                error = type(exc).__name__
                if not isinstance(exc, (requests.ConnectionError, requests.Timeout)) or attempt == retries:
                    raise
                try:
                    logger.warning("request_id=%s retry=%d error=%s", request_id, attempt + 1, error)
                except Exception:
                    pass
                time.sleep(1)
            finally:
                try:
                    if kwargs.get("stream"):
                        body = "[streaming response omitted]"
                    elif resp is not None:
                        try:
                            body = resp.json()
                        except (ValueError, TypeError):
                            body = "[non-JSON response omitted]"
                    else:
                        body = None
                    diagnostics.record_request({
                        "request_id": request_id, "method": method.upper(),
                        "url": diagnostics.safe_url(url), "params": diagnostics.safe_params(kwargs.get("params")),
                        "headers": dict(headers), "request": kwargs.get("json", "[non-JSON body omitted]"),
                        "status": resp.status_code if resp is not None else None,
                        "response": body, "error": error,
                        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                    }, case=case)
                except Exception:
                    pass

    def _headers(self):
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}
