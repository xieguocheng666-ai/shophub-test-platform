"""被测系统：本地 FastAPI 电商后端

模拟真实电商平台后端服务，作为测试框架的被测对象（SUT）。
完整业务闭环：登录(JWT) → 搜索 → 购物车 → 下单(按店铺拆单+扣库存) → 支付 → 子订单发货/确认收货。
数据存 MySQL（server/db.py 数据访问层），接口/UI 都测它。
平台模式：一个商家可开多店，商品价格库存挂店铺（store_product），订单按店铺拆成父订单+子订单。
"""
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from threading import Lock

import jwt
from anyio import to_thread
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from server import db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 同步 handler 线程并发上限由 THREADPOOL_SIZE 配置，当前默认100。
    # 多进程会叠加线程与连接需求；该值不保证避免排队或连接拒绝，需结合资源测量。
    to_thread.current_default_thread_limiter().total_tokens = int(
        os.getenv("THREADPOOL_SIZE", "100")
    )
    yield


app = FastAPI(title="My Shop API", version="0.5.0", lifespan=lifespan)

class RequestDiagnosticsMiddleware:
    """仅记录请求标识、路由模板、状态和耗时；不记录正文、查询或鉴权。"""

    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger("server.requests")
        # Uvicorn 默认仅配置自己的 logger，root 仍为 WARNING；显式提供安全日志出口。
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
            self.logger.addHandler(handler)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = next((v.decode("latin1") for k, v in scope.get("headers", []) if k.lower() == b"x-request-id"), "")
        request_id = incoming if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", incoming) else uuid4().hex
        started = time.monotonic()
        status = 500
        response_started = False

        async def correlated_send(message):
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in (b"x-request-id", b"x-test-run-id")]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                run_id = os.getenv("TEST_RUN_ID", "")
                if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", run_id):
                    headers.append((b"x-test-run-id", run_id.encode("ascii")))
                environment_id = os.getenv("TEST_ENVIRONMENT_ID", "")
                if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", environment_id):
                    headers.append((b"x-test-environment-id", environment_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, correlated_send)
        except Exception:
            # 未处理异常的默认 500 也带关联头；随后重抛原异常交给服务器记录。
            if not response_started:
                try:
                    await correlated_send({"type": "http.response.start", "status": 500,
                                           "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                    await correlated_send({"type": "http.response.body", "body": b"Internal Server Error"})
                except Exception:
                    pass
            raise
        finally:
            try:
                route = scope.get("route")
                # 路由模板不会暴露实际路径参数；未知路径不原样输出。
                path = getattr(route, "path", "[unmatched]")
                self.logger.info("run=%s environment=%s request_id=%s method=%s path=%s status=%s elapsed_ms=%.3f",
                                 os.getenv("TEST_RUN_ID", ""), os.getenv("TEST_ENVIRONMENT_ID", ""), request_id, scope["method"], path, status,
                                 (time.monotonic() - started) * 1000)
            except Exception:
                pass


app.add_middleware(RequestDiagnosticsMiddleware)

# ---------- 配置 ----------
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-me-not-for-prod")
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 30

# 支付网关行为（success / fail / timeout），仅 DEBUG_MODE=1 时可切换
PAYMENT_MODE = "success"
PAYMENT_TIMEOUT_SLEEP = 6  # timeout 模式挂起秒数，需大于客户端超时
_payment_lock = Lock()
_payment_in_flight = 0


def payment_state():
    with _payment_lock:
        return {"in_flight": _payment_in_flight, "payment_mode": PAYMENT_MODE}


class PaymentLifecycleMiddleware:
    """收到支付HTTP请求即登记，覆盖鉴权/线程池排队和完整处理阶段。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope['type'] != 'http' or scope['method'] != 'POST'
                or not re.fullmatch(r'/order/[^/]+/pay', scope['path'])):
            return await self.app(scope, receive, send)
        global _payment_in_flight
        with _payment_lock:
            _payment_in_flight += 1
        try:
            return await self.app(scope, receive, send)
        finally:
            with _payment_lock:
                _payment_in_flight -= 1


app.add_middleware(PaymentLifecycleMiddleware)

# ---------- 订单状态机 ----------
PENDING, PAID, SHIPPED, COMPLETED, CANCELED = (
    "pending", "paid", "shipped", "completed", "canceled",
)


# ---------- 请求体 ----------
class LoginRequest(BaseModel):
    username: str
    password: str


class CartItem(BaseModel):
    store_id: int
    product_id: int
    quantity: int = 1


class StoreCreate(BaseModel):
    store_name: str


class ProductCreate(BaseModel):
    name: str
    price: float = Field(ge=0, allow_inf_nan=False)
    stock: int = Field(ge=0)


class StockUpdate(BaseModel):
    stock: int = Field(ge=0)


# ---------- JWT 鉴权 ----------
def _create_token(username: str) -> str:
    payload = {
        "username": username,
        "exp": datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _require_user(authorization: str = Header(None)) -> str:
    """认证：从 Authorization: Bearer <token> 解析并校验 JWT，返回用户名。"""
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
    if not username or not db.get_user(username):
        raise HTTPException(status_code=401, detail="token 无效")
    return username


def _require_seller(user: str = Depends(_require_user)) -> str:
    """授权：只有 seller 角色能开店/上架/发货。角色从数据库实时查，不信任客户端 token。"""
    u = db.get_user(user)
    if u["role"] != "seller":
        raise HTTPException(status_code=403, detail="需要商家权限")
    return user


def _check_store_owner(store_id: int, user: str) -> dict:
    """校验当前 seller 是 store_id 的 owner，返回店铺信息（水平越权防护）。"""
    store = db.get_store(store_id)
    if not store:
        raise HTTPException(status_code=404, detail="店铺不存在")
    if store["owner_username"] != user:
        raise HTTPException(status_code=403, detail="无权操作他人店铺")
    return store


# ---------- 健康检查 ----------
@app.get("/health")
def health():
    result = {"status": "ok"}
    if os.getenv("TEST_ENVIRONMENT_ID"):
        result.update(environment_id=os.environ["TEST_ENVIRONMENT_ID"],
                      database=os.getenv("DB_NAME", "shop"))
    return result


# ---------- 认证 ----------
@app.post("/login")
def login(req: LoginRequest):
    user = db.get_user(req.username)
    if not user or not db.verify_password(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"token": _create_token(req.username), "nickname": user["nickname"]}


@app.get("/me")
def me(user: str = Depends(_require_user)):
    u = db.get_user(user)
    return {"username": user, "nickname": u["nickname"], "role": u["role"]}


# ---------- 商品 ----------
@app.get("/products")
def search_products(keyword: str = "", page: int = 1, page_size: int = 20):
    return db.list_products(keyword.strip(), page, page_size)


@app.get("/stores/{store_id}/products/{product_id}")
def get_store_product_public(store_id: int, product_id: int):
    """买家视角的商品详情：查某店铺的某个在售商品（公开，无需登录）。"""
    item = db.get_store_product(store_id, product_id)
    if not item:
        raise HTTPException(status_code=404, detail="商品不存在")
    return item


# ---------- 购物车 ----------
@app.post("/cart")
def add_to_cart(item: CartItem, user: str = Depends(_require_user)):
    return db.cart_add(user, item.store_id, item.product_id, item.quantity)


@app.get("/cart")
def get_cart(user: str = Depends(_require_user)):
    return db.cart_get(user)


@app.delete("/cart/{store_id}/{product_id}")
def remove_from_cart(store_id: int, product_id: int, user: str = Depends(_require_user)):
    return db.cart_remove(user, store_id, product_id)


# ---------- 订单 ----------
@app.post("/order")
def create_order(user: str = Depends(_require_user)):
    return db.create_order(user)


@app.get("/orders")
def list_orders(user: str = Depends(_require_user), page: int = 1, page_size: int = 20):
    return db.list_orders(user, page, page_size)


@app.get("/order/{order_id}")
def get_order_detail(order_id: int, user: str = Depends(_require_user)):
    order = db.get_order(user, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    return db.get_order_detail(order_id)


# ---------- 支付（模拟第三方支付网关，故障注入） ----------
@app.post("/order/{order_id}/pay")
def pay_order(order_id: int, user: str = Depends(_require_user)):
    order = db.get_order(user, order_id)  # 只查自己的订单，天然防越权
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order["status"] != PENDING:
        raise HTTPException(status_code=400, detail="订单状态不允许支付")

    if PAYMENT_MODE == "timeout":
        # 模拟网关无响应：挂起直到客户端超时放弃，订单保持 pending 不变。
        # 不能在 sleep 后再读全局 PAYMENT_MODE（可能已被后续测试切换），否则会误走 fail/success。
        time.sleep(PAYMENT_TIMEOUT_SLEEP)
        return {"status": "timeout", "order_id": order_id}

    if PAYMENT_MODE == "fail":
        db.cancel_order(order_id)
        return {"status": "failed", "reason": "余额不足", "order_id": order_id}

    db.pay_order(order_id)
    return {"status": "paid", "order_id": order_id}


# ---------- 发货 / 确认收货（子订单层） ----------
@app.post("/sub-orders/{sub_order_id}/ship")
def ship_sub_order(sub_order_id: int, user: str = Depends(_require_seller)):
    """发货：需 seller，且子订单属于该 seller 的店铺（水平越权防护）。"""
    sub = db.get_sub_order(sub_order_id)
    if not sub:
        raise HTTPException(status_code=404, detail="子订单不存在")
    _check_store_owner(sub["store_id"], user)
    if sub["status"] != PAID:
        raise HTTPException(
            status_code=400,
            detail=f"子订单状态不允许从 {sub['status']} 变为 shipped",
        )
    db.ship_sub_order(sub_order_id)
    return {"status": SHIPPED, "sub_order_id": sub_order_id}


@app.post("/sub-orders/{sub_order_id}/confirm")
def confirm_sub_order(sub_order_id: int, user: str = Depends(_require_user)):
    """确认收货：买家对自己的子订单操作；全部子订单完成后父订单自动完成。"""
    sub = db.get_sub_order(sub_order_id)
    if not sub:
        raise HTTPException(status_code=404, detail="子订单不存在")
    parent = db.get_parent_order(sub["parent_order_id"])
    if parent["username"] != user:
        raise HTTPException(status_code=403, detail="无权操作他人订单")
    if sub["status"] != SHIPPED:
        raise HTTPException(
            status_code=400,
            detail=f"子订单状态不允许从 {sub['status']} 变为 completed",
        )
    db.confirm_sub_order(sub_order_id)
    return {"status": COMPLETED, "sub_order_id": sub_order_id}


# ---------- 店铺管理（seller） ----------
@app.post("/stores")
def create_store(req: StoreCreate, user: str = Depends(_require_seller)):
    return db.create_store(user, req.store_name)


@app.get("/stores")
def list_my_stores(user: str = Depends(_require_seller)):
    return {"stores": db.list_stores_by_owner(user)}


@app.post("/stores/{store_id}/products")
def add_product(store_id: int, req: ProductCreate, user: str = Depends(_require_seller)):
    _check_store_owner(store_id, user)
    return db.add_store_product(store_id, req.name, req.price, req.stock)


@app.get("/stores/{store_id}/products")
def list_store_products(store_id: int, user: str = Depends(_require_seller)):
    _check_store_owner(store_id, user)
    return {"items": db.list_store_products(store_id)}


@app.patch("/stores/{store_id}/products/{product_id}/stock")
def update_stock(
    store_id: int, product_id: int, req: StockUpdate, user: str = Depends(_require_seller)
):
    _check_store_owner(store_id, user)
    db.update_stock(store_id, product_id, req.stock)
    return {"store_id": store_id, "product_id": product_id, "stock": req.stock}


# ---------- 故障注入开关（仅 DEBUG 环境启用） ----------
if os.getenv("DEBUG_MODE") == "1":

    app.get("/debug/payment-state")(payment_state)

    class PaymentModeRequest(BaseModel):
        mode: str

    @app.post("/debug/payment-mode")
    def set_payment_mode(req: PaymentModeRequest):
        global PAYMENT_MODE
        if req.mode not in ("success", "fail", "timeout"):
            raise HTTPException(status_code=400, detail="mode 必须是 success/fail/timeout")
        PAYMENT_MODE = req.mode
        return {"payment_mode": PAYMENT_MODE}

    @app.post("/debug/reset")
    def reset_data():
        global PAYMENT_MODE
        db.reset_data()
        PAYMENT_MODE = "success"
        return {"status": "reset"}

    @app.post("/debug/cancel-expired")
    def cancel_expired(seconds: int = 0):
        """手动触发超时订单取消（与 Celery worker 周期扫描同一份逻辑）。

        seconds=0 取消所有 pending 订单，供测试/演示用。
        """
        return db.cancel_expired_orders(seconds)


# ---------- 前端静态页面 ----------
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
