# SUT 三特性改造 + 接口测试四组 · 实施计划

> ⚠️ **已过时（历史快照）**：写于 2026-08-14 阶段 0，计划已执行完毕。代码已于 2026-08-31 经历 MySQL 化 + RBAC 改造，本文档中的完整代码（内存 dict + threading.Lock）已全部被 `server/db.py` + `server/main.py` 现行实现取代。仅作阶段 0 历史参考，勿当现行依据。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把被测系统（SUT）从"玩具电商"升级为含 JWT 鉴权、订单状态机、模拟支付网关、并发防超卖、越权校验的真实靶子，并让接口测试展开四组真实场景。

**Architecture:** 单文件 `server/main.py` 承载 SUT 全部特性（内存存储、threading.Lock 防超卖、JWT 无状态鉴权）；测试框架侧 `api_client.py` 持有 JWT 并统一加 `Authorization` header，`conftest.py` 提供登录态与支付模式 fixture，`test_api.py` 分四组覆盖鉴权/状态/支付异常/并发。

**Tech Stack:** FastAPI + pyjwt（新增）+ uvicorn；pytest + requests + PyYAML。

> ⚠️ 本项目尚未 `git init`，各 Task 的完成标准为"验证命令通过"，不做 git commit（由用户决定何时初始化仓库）。

---

## 文件结构

| 文件 | 责任 | 变更 |
|------|------|------|
| `requirements.txt` | 依赖清单 | 加 PyJWT |
| `config/config.yaml` | 环境配置 | 加 secret_key |
| `server/main.py` | 被测系统（全部接口 + 特性） | 重写（JWT/状态机/支付/并发/越权） |
| `api/api_client.py` | HTTP 客户端封装 | 重写（header token + 业务方法） |
| `conftest.py` | 全局 fixture | 改（fixture 粒度 + payment_mode） |
| `api/test_api.py` | 接口测试用例 | 重写（四组） |

---

## 前置：启动 SUT（每个 Task 的验证都依赖）

```bash
cd /d/my-test-framework
DEBUG_MODE=1 .venv/Scripts/python -m uvicorn server.main:app --port 8000
```

> `DEBUG_MODE=1` 用于启用 `/debug/payment-mode` 故障注入接口。验证时保持服务运行。

---

## Task 1: 加 pyjwt 依赖 + config 加 secret_key

**Files:**
- Modify: `requirements.txt`
- Modify: `config/config.yaml`

- [ ] **Step 1: 加依赖**

`requirements.txt` 末尾追加一行：

```text
# JWT 鉴权
PyJWT>=2.8
```

`config/config.yaml` 末尾追加：

```yaml
# JWT 签名密钥（与 server 的 SECRET_KEY 环境变量默认值保持一致）
secret_key: dev-secret-key-change-me
```

- [ ] **Step 2: 安装依赖并验证**

```bash
cd /d/my-test-framework
.venv/Scripts/python -m pip install "PyJWT>=2.8"
.venv/Scripts/python -c "import jwt; print(jwt.__version__)"
```

Expected: 打印 pyjwt 版本号，无报错。

---

## Task 2: 重写 server/main.py（JWT + 状态机 + 支付网关 + 并发 + 越权）

**Files:**
- Rewrite: `server/main.py`

用以下完整代码覆盖 `server/main.py`：

```python
"""被测系统：本地 FastAPI 电商后端

模拟真实电商后端服务，作为测试框架的被测对象（SUT）。
完整业务闭环：登录(JWT) → 搜索 → 购物车 → 下单(待支付+扣库存) → 支付 → 状态流转。
内存存储，重启即清空，够测试用即可。
"""
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="My Shop API", version="0.3.0")

# ---------- 配置 ----------
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 30

# 支付网关行为（success / fail / timeout），仅 DEBUG_MODE=1 时可切换
PAYMENT_MODE = "success"
PAYMENT_TIMEOUT_SLEEP = 6  # timeout 模式挂起秒数，需大于客户端超时

# 并发防超卖：保护"校验库存 + 扣减"临界区的锁
STOCK_LOCK = threading.Lock()

# ---------- 订单状态机 ----------
PENDING, PAID, SHIPPED, COMPLETED, CANCELED = (
    "pending", "paid", "shipped", "completed", "canceled",
)
TRANSITIONS = {
    PENDING: {PAID, CANCELED},
    PAID: {SHIPPED},
    SHIPPED: {COMPLETED},
}

# ---------- 内存"数据库" ----------
USERS = {
    "alice": {"password": "alice123", "nickname": "爱丽丝"},
    "bob": {"password": "bob123", "nickname": "鲍勃"},
}
PRODUCTS = [
    {"id": 1, "name": "联想小新 Pro16 笔记本", "price": 5499.0, "stock": 50},
    {"id": 2, "name": "罗技 K845 机械键盘", "price": 299.0, "stock": 100},
    {"id": 3, "name": "罗技 G304 无线鼠标", "price": 149.0, "stock": 200},
    {"id": 4, "name": "AOC 27 寸 2K 显示器", "price": 1099.0, "stock": 30},
    {"id": 5, "name": "闪迪 64G USB3.1 U盘", "price": 39.9, "stock": 500},
    {"id": 6, "name": "品胜 Type-C 数据线", "price": 19.9, "stock": 1000},
    {"id": 7, "name": "索尼 WH-1000XM4 降噪耳机", "price": 1999.0, "stock": 40},
    {"id": 8, "name": "罗技 C920 高清摄像头", "price": 399.0, "stock": 80},
]

CARTS: dict[str, dict[int, int]] = {}
ORDERS: dict[str, list] = {}
ORDER_SEQ = 0


# ---------- 请求体 ----------
class LoginRequest(BaseModel):
    username: str
    password: str


class CartItem(BaseModel):
    product_id: int
    quantity: int = 1


# ---------- JWT 鉴权 ----------
def _create_token(username: str) -> str:
    payload = {
        "username": username,
        "exp": datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _require_user(authorization: str = Header(None)) -> str:
    """从 Authorization: Bearer <token> 解析并校验 JWT，返回用户名。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 token")
    token = authorization[len("Bearer "):]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="token 无效")
    username = payload.get("username")
    if not username or username not in USERS:
        raise HTTPException(status_code=401, detail="token 无效")
    return username


# ---------- 订单工具 ----------
def _find_order(username: str, order_id: int) -> dict:
    """按 order_id 找订单：不属于自己 → 403，不存在 → 404。"""
    for orders in ORDERS.values():
        for order in orders:
            if order["order_id"] == order_id:
                if order["username"] != username:
                    raise HTTPException(status_code=403, detail="无权操作他人订单")
                return order
    raise HTTPException(status_code=404, detail="订单不存在")


def _transition(order: dict, target: str) -> None:
    """校验并执行状态迁移，非法迁移 → 400。"""
    allowed = TRANSITIONS.get(order["status"], set())
    if target not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"订单状态不允许从 {order['status']} 变为 {target}",
        )
    order["status"] = target


def _restore_stock(order: dict) -> None:
    """支付失败/取消时回补库存。"""
    with STOCK_LOCK:
        for item in order["items"]:
            product = next(p for p in PRODUCTS if p["id"] == item["product_id"])
            product["stock"] += item["quantity"]


# ---------- 健康检查 ----------
@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- 认证 ----------
@app.post("/login")
def login(req: LoginRequest):
    user = USERS.get(req.username)
    if not user or user["password"] != req.password:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"token": _create_token(req.username), "nickname": user["nickname"]}


@app.get("/me")
def me(user: str = Depends(_require_user)):
    return {"username": user, "nickname": USERS[user]["nickname"]}


# ---------- 商品 ----------
@app.get("/products")
def search_products(keyword: str = ""):
    kw = keyword.strip().lower()
    if not kw:
        return {"items": PRODUCTS}
    return {"items": [p for p in PRODUCTS if kw in p["name"].lower()]}


@app.get("/products/{product_id}")
def get_product(product_id: int):
    product = next((p for p in PRODUCTS if p["id"] == product_id), None)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    return product


# ---------- 购物车 ----------
@app.post("/cart")
def add_to_cart(item: CartItem, user: str = Depends(_require_user)):
    product = next((p for p in PRODUCTS if p["id"] == item.product_id), None)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    if item.quantity <= 0:
        raise HTTPException(status_code=400, detail="数量必须大于 0")
    cart = CARTS.setdefault(user, {})
    new_qty = cart.get(item.product_id, 0) + item.quantity
    if new_qty > product["stock"]:
        raise HTTPException(
            status_code=400,
            detail=f"{product['name']} 库存不足（仅剩 {product['stock']} 件）",
        )
    cart[item.product_id] = new_qty
    return _cart_payload(user)


@app.get("/cart")
def get_cart(user: str = Depends(_require_user)):
    return _cart_payload(user)


@app.delete("/cart/{product_id}")
def remove_from_cart(product_id: int, user: str = Depends(_require_user)):
    cart = CARTS.setdefault(user, {})
    if product_id not in cart:
        raise HTTPException(status_code=404, detail="购物车中没有该商品")
    del cart[product_id]
    return _cart_payload(user)


def _cart_payload(username: str) -> dict:
    items = []
    total = 0.0
    for pid, qty in CARTS.get(username, {}).items():
        product = next(p for p in PRODUCTS if p["id"] == pid)
        items.append(
            {
                "product_id": pid,
                "name": product["name"],
                "price": product["price"],
                "quantity": qty,
                "subtotal": round(product["price"] * qty, 2),
            }
        )
        total += product["price"] * qty
    return {"items": items, "total": round(total, 2)}


# ---------- 订单（状态机 + 并发扣库存） ----------
@app.post("/order")
def create_order(user: str = Depends(_require_user)):
    cart = CARTS.get(user, {})
    if not cart:
        raise HTTPException(status_code=400, detail="购物车为空，无法下单")

    with STOCK_LOCK:  # 原子：校验 + 扣减，防超卖
        for pid, qty in cart.items():
            product = next(p for p in PRODUCTS if p["id"] == pid)
            if product["stock"] < qty:
                raise HTTPException(
                    status_code=400,
                    detail=f"{product['name']} 库存不足（仅剩 {product['stock']} 件）",
                )
        for pid, qty in cart.items():
            next(p for p in PRODUCTS if p["id"] == pid)["stock"] -= qty

    global ORDER_SEQ
    ORDER_SEQ += 1
    items = []
    total = 0.0
    for pid, qty in cart.items():
        product = next(p for p in PRODUCTS if p["id"] == pid)
        items.append(
            {
                "product_id": pid,
                "name": product["name"],
                "price": product["price"],
                "quantity": qty,
            }
        )
        total += product["price"] * qty

    order = {
        "order_id": ORDER_SEQ,
        "username": user,
        "items": items,
        "total": round(total, 2),
        "status": PENDING,
    }
    ORDERS.setdefault(user, []).append(order)
    CARTS[user] = {}
    return order


@app.get("/orders")
def list_orders(user: str = Depends(_require_user)):
    return {"orders": ORDERS.get(user, [])}


# ---------- 支付（模拟第三方支付网关，故障注入） ----------
@app.post("/order/{order_id}/pay")
def pay_order(order_id: int, user: str = Depends(_require_user)):
    order = _find_order(user, order_id)
    if order["status"] != PENDING:
        raise HTTPException(status_code=400, detail="订单状态不允许支付")

    if PAYMENT_MODE == "timeout":
        time.sleep(PAYMENT_TIMEOUT_SLEEP)  # 超过客户端超时，触发框架重试

    if PAYMENT_MODE == "fail":
        _restore_stock(order)
        _transition(order, CANCELED)
        return {"status": "failed", "reason": "余额不足", "order_id": order_id}

    _transition(order, PAID)
    return {"status": "paid", "order_id": order_id}


# ---------- 发货 / 确认收货（越权由 _find_order 保证） ----------
@app.post("/order/{order_id}/ship")
def ship_order(order_id: int, user: str = Depends(_require_user)):
    order = _find_order(user, order_id)
    _transition(order, SHIPPED)
    return {"status": "shipped", "order_id": order_id}


@app.post("/order/{order_id}/confirm")
def confirm_order(order_id: int, user: str = Depends(_require_user)):
    order = _find_order(user, order_id)
    _transition(order, COMPLETED)
    return {"status": "completed", "order_id": order_id}


# ---------- 故障注入开关（仅 DEBUG 环境启用） ----------
if os.getenv("DEBUG_MODE") == "1":

    class PaymentModeRequest(BaseModel):
        mode: str

    @app.post("/debug/payment-mode")
    def set_payment_mode(req: PaymentModeRequest):
        global PAYMENT_MODE
        if req.mode not in ("success", "fail", "timeout"):
            raise HTTPException(status_code=400, detail="mode 必须是 success/fail/timeout")
        PAYMENT_MODE = req.mode
        return {"payment_mode": PAYMENT_MODE}


# ---------- 前端静态页面 ----------
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
```

- [ ] **Step 2: 重启服务**

先停掉旧 uvicorn 进程，再以 `DEBUG_MODE=1` 重启（见前置）。

- [ ] **Step 3: 验证 JWT 鉴权**

```bash
curl -s -X POST http://127.0.0.1:8000/login -H "Content-Type: application/json" -d '{"username":"alice","password":"alice123"}'
```

Expected: 返回含 JWT 的 token（形如 `eyJ...`，不再是 `token-alice-1`）。

```bash
# 无 token 调 /me → 401
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/me
```

Expected: `401`

- [ ] **Step 4: 验证状态机 + 支付 + 越权（用一个 bash 脚本走全链路）**

```bash
BASE=http://127.0.0.1:8000
TOKEN=$(curl -s -X POST $BASE/login -H "Content-Type: application/json" -d '{"username":"alice","password":"alice123"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
AUTH="Authorization: Bearer $TOKEN"
# 加购 + 下单 → pending
curl -s -X POST $BASE/cart -H "$AUTH" -H "Content-Type: application/json" -d '{"product_id":2,"quantity":1}' > /dev/null
curl -s -X POST $BASE/order -H "$AUTH"
```

Expected: 订单 `"status":"pending"`。

继续手动验证：支付 → paid、ship → shipped、confirm → completed、fail 模式 → canceled + 库存回补、越权 → 403。若手测繁琐，可直接跳到 Task 5 用 pytest 覆盖。

---

## Task 3: 重写 api/api_client.py

**Files:**
- Rewrite: `api/api_client.py`

用以下完整代码覆盖：

```python
"""ApiClient：对 requests 的封装，接口测试的统一入口。

核心对象：requests.Session。ApiClient 内部持有它，并维护登录后的 JWT，
受保护接口自动带上 Authorization: Bearer <token>。
"""
import logging
import time

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

    def product(self, product_id: int):
        return self._request("GET", f"/products/{product_id}")

    def cart_add(self, product_id: int, quantity: int = 1):
        return self._request(
            "POST", "/cart", headers=self._headers(),
            json={"product_id": product_id, "quantity": quantity},
        )

    def cart_get(self):
        return self._request("GET", "/cart", headers=self._headers())

    def cart_remove(self, product_id: int):
        return self._request("DELETE", f"/cart/{product_id}", headers=self._headers())

    def order_create(self):
        return self._request("POST", "/order", headers=self._headers())

    def order_pay(self, order_id: int):
        return self._request("POST", f"/order/{order_id}/pay", headers=self._headers())

    def order_ship(self, order_id: int):
        return self._request("POST", f"/order/{order_id}/ship", headers=self._headers())

    def order_confirm(self, order_id: int):
        return self._request("POST", f"/order/{order_id}/confirm", headers=self._headers())

    def orders(self):
        return self._request("GET", "/orders", headers=self._headers())

    def set_payment_mode(self, mode: str):
        return self._request("POST", "/debug/payment-mode", json={"mode": mode})
```

- [ ] **Step 2: 验证可导入**

```bash
cd /d/my-test-framework
.venv/Scripts/python -c "from api.api_client import ApiClient; print('ok')"
```

Expected: `ok`

---

## Task 4: 改 conftest.py（fixture 粒度 + payment_mode）

**Files:**
- Rewrite: `conftest.py`

用以下完整代码覆盖：

```python
"""全局 fixture：conftest.py 会被 pytest 自动加载，所有测试用例共用。

fixture 生命周期：config/base_url 用 session 级（只读、可复用）；
api_client/login_user/payment_mode 用 function 级（每个测试独立，避免状态互相污染）。
"""
from pathlib import Path

import pytest
import yaml

from api.api_client import ApiClient

ROOT_DIR = Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def config():
    with open(ROOT_DIR / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def base_url(config):
    return config["base_url"]


@pytest.fixture()
def api_client(base_url):
    """每个测试独立的客户端，token 互不串扰。"""
    return ApiClient(base_url)


@pytest.fixture()
def login_user(api_client, config):
    """登录默认账号 alice，返回 token 和用户名。"""
    user = config["users"]["alice"]
    resp = api_client.login(user["username"], user["password"])
    assert resp.status_code == 200, resp.text
    return {"token": resp.json()["token"], "username": user["username"]}


@pytest.fixture()
def payment_mode(api_client):
    """切换支付网关模式，测试结束后恢复为 success。"""
    def _set(mode):
        resp = api_client.set_payment_mode(mode)
        assert resp.status_code == 200, resp.text
        return resp

    _set("success")
    yield _set
    _set("success")
```

- [ ] **Step 2: 验证 fixture 可收集**

```bash
cd /d/my-test-framework
.venv/Scripts/python -m pytest --collect-only -q 2>&1 | tail -5
```

Expected: 能看到测试被收集（此时 `test_api.py` 还是旧的，稍后 Task 5 重写；能收集即通过）。

---

## Task 5: 重写 api/test_api.py（四组测试）

**Files:**
- Rewrite: `api/test_api.py`

用以下完整代码覆盖：

```python
"""被测系统接口测试：基础 + 四组真实场景（鉴权/状态机/支付异常/并发）。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import jwt as pyjwt
import pytest
import requests
import yaml

from api.api_client import ApiClient

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_login_cases():
    with open(ROOT_DIR / "data" / "users.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cases = []
    for group in ("valid_login", "invalid_password", "unknown_user"):
        for item in data[group]:
            cases.append((item["username"], item["password"], item["expect_code"]))
    return cases


# ---------- 基础 ----------
def test_health(api_client):
    resp = api_client.health()
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize("username,password,expect_code", _load_login_cases())
def test_login(username, password, expect_code, api_client):
    resp = api_client.login(username, password)
    assert resp.status_code == expect_code


# ---------- 组 1：鉴权 ----------
def test_me_without_token(base_url):
    resp = requests.get(f"{base_url}/me")
    assert resp.status_code == 401


def test_me_with_forged_token(base_url):
    resp = requests.get(f"{base_url}/me", headers={"Authorization": "Bearer forged.token"})
    assert resp.status_code == 401


def test_me_with_wrong_signature(base_url):
    token = pyjwt.encode({"username": "alice"}, "wrong-secret", algorithm="HS256")
    resp = requests.get(f"{base_url}/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_me_with_expired_token(base_url, config):
    token = pyjwt.encode(
        {"username": "alice", "exp": datetime.utcnow() - timedelta(seconds=10)},
        config["secret_key"], algorithm="HS256",
    )
    resp = requests.get(f"{base_url}/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


# ---------- 组 2：状态机 ----------
def test_order_full_flow(api_client, login_user):
    api_client.cart_add(2, 1)
    order_resp = api_client.order_create()
    assert order_resp.status_code == 200
    order_id = order_resp.json()["order_id"]
    assert order_resp.json()["status"] == "pending"

    assert api_client.order_pay(order_id).json()["status"] == "paid"
    assert api_client.order_ship(order_id).json()["status"] == "shipped"
    assert api_client.order_confirm(order_id).json()["status"] == "completed"


def test_illegal_transition_rejected(api_client, login_user):
    api_client.cart_add(3, 1)
    order_id = api_client.order_create().json()["order_id"]
    # 待支付直接确认收货 → 400
    resp = api_client.order_confirm(order_id)
    assert resp.status_code == 400


def test_cannot_operate_others_order(api_client, config):
    alice = config["users"]["alice"]
    api_client.login(alice["username"], alice["password"])
    api_client.cart_add(2, 1)
    order_id = api_client.order_create().json()["order_id"]

    bob = config["users"]["bob"]
    api_client.login(bob["username"], bob["password"])
    # bob 付 alice 的订单 → 403
    resp = api_client.order_pay(order_id)
    assert resp.status_code == 403


# ---------- 组 3：支付异常 ----------
def test_pay_fail_cancels_and_restores_stock(api_client, login_user, payment_mode):
    payment_mode("fail")
    product_id = 5
    stock_before = api_client.product(product_id).json()["stock"]
    api_client.cart_add(product_id, 2)
    order_id = api_client.order_create().json()["order_id"]

    resp = api_client.order_pay(order_id)
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    # 订单已取消
    assert api_client.orders().json()["orders"][-1]["status"] == "canceled"
    # 库存回补
    assert api_client.product(product_id).json()["stock"] == stock_before


def test_pay_fail_then_repay_rejected(api_client, login_user, payment_mode):
    payment_mode("fail")
    api_client.cart_add(5, 1)
    order_id = api_client.order_create().json()["order_id"]
    api_client.order_pay(order_id)
    # 已取消订单再支付 → 400
    payment_mode("success")
    resp = api_client.order_pay(order_id)
    assert resp.status_code == 400


def test_pay_timeout_triggers_retry(base_url, login_user, payment_mode):
    payment_mode("timeout")
    # 用短超时 client 加速（1s < 服务端 6s 挂起）
    client = ApiClient(base_url, timeout=1, retry=2)
    client.token = login_user["token"]
    client.cart_add(2, 1)
    order_id = client.order_create().json()["order_id"]
    with pytest.raises(requests.Timeout):
        client.order_pay(order_id)


# ---------- 组 4：并发防超卖 ----------
def test_concurrent_order_no_oversell(base_url, config):
    bob = config["users"]["bob"]
    login_resp = requests.post(
        f"{base_url}/login",
        json={"username": bob["username"], "password": bob["password"]},
    )
    token = login_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    product_id = 3
    stock = requests.get(f"{base_url}/products/{product_id}").json()["stock"]

    def place_order(_):
        requests.post(
            f"{base_url}/cart", headers=headers,
            json={"product_id": product_id, "quantity": 1},
        )
        return requests.post(f"{base_url}/order", headers=headers).status_code

    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(place_order, range(stock + 30)))

    success = sum(1 for c in results if c == 200)
    final_stock = requests.get(f"{base_url}/products/{product_id}").json()["stock"]
    assert success <= stock
    assert final_stock >= 0
```

- [ ] **Step 2: 跑测试并确认全绿**

```bash
cd /d/my-test-framework
.venv/Scripts/python -m pytest api/test_api.py -v
```

Expected: 全部 PASS（含 timeout 用例约 3-5 秒）。

---

## Task 6: 全量回归 + 前端页面适配检查

- [ ] **Step 1: 跑全量测试**

```bash
cd /d/my-test-framework
.venv/Scripts/python -m pytest -v
```

Expected: 全部 PASS。

- [ ] **Step 2: 前端页面适配检查**

前端 `login.html` / `index.html` / `cart.html` 目前仍用 `?token=` 查询参数调接口，与新的 `Authorization` header 鉴权不兼容。需检查并适配（登录后 token 存 localStorage，请求加 header）。**若本阶段聚焦接口测试，前端适配可延后到阶段 2（UI 模块）一并处理**——当前只需确认 `pytest` 全绿，前端不阻塞本阶段验收。

- [ ] **Step 3: 更新进度表**

更新 `D:\学习计划与进度\progress-tracker.md`：阶段 0 完成，接口测试四组跑通。

---

## 自检记录

- **Spec 覆盖**：JWT（Task 2/组1）、状态机（Task 2/组2）、支付网关（Task 2/组3）、并发（Task 2/组4）、越权（Task 2/组2 `test_cannot_operate_others_order`）均有用例对应。
- **类型一致性**：`_find_order`/`_transition`/`_restore_stock` 在 server 与测试中命名一致；`ApiClient` 方法名（`cart_add`/`order_create`/`order_pay`/`order_ship`/`order_confirm`/`orders`/`set_payment_mode`）在 Task 3 定义、Task 5 使用，一致。
- **已知取舍**：前端页面 header 适配延后到阶段 2；token 自动刷新未实现（YAGNI，测试显式构造过期 token 即可）。
