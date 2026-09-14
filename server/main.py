"""被测系统：本地 FastAPI 电商后端

模拟真实电商平台后端服务，作为测试框架的被测对象（SUT）。
完整业务闭环：登录(JWT) → 搜索 → 购物车 → 下单(按店铺拆单+扣库存) → 支付 → 子订单发货/确认收货。
数据存 MySQL（server/db.py 数据访问层），接口/UI 都测它。
平台模式：一个商家可开多店，商品价格库存挂店铺（store_product），订单按店铺拆成父订单+子订单。
"""
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server import db

load_dotenv()

app = FastAPI(title="My Shop API", version="0.5.0")

# ---------- 配置 ----------
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-me-not-for-prod")
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 30

# 支付网关行为（success / fail / timeout），仅 DEBUG_MODE=1 时可切换
PAYMENT_MODE = "success"
PAYMENT_TIMEOUT_SLEEP = 6  # timeout 模式挂起秒数，需大于客户端超时

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
    price: float
    stock: int


class StockUpdate(BaseModel):
    stock: int


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
    return {"status": "ok"}


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
