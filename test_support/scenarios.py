"""本例资源的创建、真实业务状态准备与按归属清理。"""
from dataclasses import dataclass
from decimal import Decimal

from api.api_client import ApiClient
from server.db import hash_password
from test_support.factories import DataFactory, UserParams


def _json_ok(response):
    if response.status_code != 200:
        raise AssertionError(f"测试准备接口失败：HTTP {response.status_code}")
    return response.json()


@dataclass
class Account:
    params: UserParams
    client: ApiClient
    store_id: int | None = None

    @property
    def username(self):
        return self.params.username


@dataclass(frozen=True)
class Product:
    product_id: int
    store_id: int
    price: Decimal
    stock: int
    seller: Account


@dataclass(frozen=True)
class Order:
    order_id: int
    sub_orders: tuple
    buyer: Account


class TestData:
    __test__ = False

    def __init__(self, base_url, db):
        self.base_url = base_url
        self.db = db
        self.factory = DataFactory()
        self._accounts = []
        self._product_ids = set()
        self._new_product_names = set()
        self._closed = False

    @property
    def usernames(self):
        return tuple(account.username for account in self._accounts)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        # 同时报告原始异常和清理异常，避免清理覆盖业务失败。
        try:
            self.cleanup()
        except Exception as cleanup_error:
            if exc is not None:
                raise BaseExceptionGroup("业务失败且资源清理失败", [exc, cleanup_error]) from None
            raise
        return False

    def _user(self, role, **overrides):
        if self._closed:
            raise RuntimeError("数据上下文已经清理")
        params = self.factory.user(role=role, **overrides)
        client = ApiClient(self.base_url, retry=0)
        account = Account(params, client)
        try:
            with self.db.conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (username,password,nickname,role) VALUES (%s,%s,%s,%s)",
                    (params.username, hash_password(params.password), params.nickname, params.role),
                )
            # 记录已成功写入的用户；后续登录失败仍可按归属清理。
            self._accounts.append(account)
            _json_ok(client.login(params.username, params.password))
            return account
        except BaseException:
            client.session.close()
            raise

    def buyer(self, **overrides):
        return self._user("buyer", **overrides)

    def seller(self, **overrides):
        return self._user("seller", **overrides)

    def _require_owned(self, account, role):
        if self._closed or not any(account is owned for owned in self._accounts):
            raise ValueError("必须使用本例创建且尚未清理的账号")
        if account.params.role != role:
            raise ValueError(f"需要{role}角色")

    def product(self, seller, **overrides):
        self._require_owned(seller, "seller")
        params = self.factory.product(**overrides)
        # 商城按名称复用全局商品；独立准备器明确拒绝复用，不能把关联当成所有权。
        if self.db.scalar("SELECT COUNT(*) FROM products WHERE name=%s", (params.name,)):
            raise ValueError("商品名称已存在；独立工厂只创建新商品，复用场景请使用业务客户端")
        if seller.store_id is None:
            store = _json_ok(seller.client.create_store(f"{self.factory.namespace}_store"))
            seller.store_id = store["store_id"]
        self._new_product_names.add(params.name)
        data = _json_ok(seller.client.add_product(
            seller.store_id, params.name, float(params.price), params.stock,
        ))
        self._product_ids.add(data["product_id"])
        return Product(data["product_id"], seller.store_id, params.price, params.stock, seller)

    def order(self, buyer, items, *, state="pending"):
        if state not in {"pending", "paid", "shipped", "completed"}:
            raise ValueError("state只支持pending/paid/shipped/completed")
        self._require_owned(buyer, "buyer")
        if not items:
            raise ValueError("订单明细不能为空")
        for product, quantity in items:
            self._require_owned(product.seller, "seller")
            _json_ok(buyer.client.cart_add(product.store_id, product.product_id, quantity))
        data = _json_ok(buyer.client.order_create())
        order = Order(data["order_id"], tuple(data["sub_orders"]), buyer)
        if state != "pending":
            _json_ok(buyer.client.order_pay(order.order_id))
        if state in {"shipped", "completed"}:
            sellers = {product.store_id: product.seller for product, _ in items}
            for sub in order.sub_orders:
                _json_ok(sellers[sub["store_id"]].client.sub_order_ship(sub["sub_order_id"]))
        if state == "completed":
            for sub in order.sub_orders:
                _json_ok(buyer.client.sub_order_confirm(sub["sub_order_id"]))
        return order

    def cleanup(self):
        if self._closed:
            return
        names = self.usernames
        try:
            if names:
                self._delete_owned(names)
            self._closed = True
        finally:
            for account in self._accounts:
                account.client.session.close()

    def _delete_owned(self, names):
        """同一事务内按外键依赖反序删除，失败回滚以便重试和排查。"""
        marks = ",".join(["%s"] * len(names))
        connection = self.db.conn
        connection.begin()
        try:
            with connection.cursor() as cursor:
                product_ids = set(self._product_ids)
                if self._new_product_names:
                    # 请求可能已写入但响应丢失：只回查创建前确认不存在的名称及本例店铺。
                    product_names = tuple(self._new_product_names)
                    name_marks = ",".join(["%s"] * len(product_names))
                    cursor.execute(
                        "SELECT p.id FROM products p JOIN store_product sp ON p.id=sp.product_id "
                        "JOIN stores s ON s.store_id=sp.store_id "
                        f"WHERE p.name IN ({name_marks}) AND s.owner_username IN ({marks})", product_names + names,
                    )
                    product_ids.update(row["id"] for row in cursor.fetchall())
                cursor.execute(
                    "DELETE oi FROM order_items oi JOIN sub_orders so ON oi.sub_order_id=so.sub_order_id "
                    f"JOIN orders o ON o.order_id=so.parent_order_id WHERE o.username IN ({marks})", names,
                )
                cursor.execute(
                    "DELETE so FROM sub_orders so JOIN orders o ON o.order_id=so.parent_order_id "
                    f"WHERE o.username IN ({marks})", names,
                )
                cursor.execute(f"DELETE FROM orders WHERE username IN ({marks})", names)
                cursor.execute(f"DELETE FROM carts WHERE username IN ({marks})", names)
                cursor.execute(
                    "DELETE sp FROM store_product sp JOIN stores s ON s.store_id=sp.store_id "
                    f"WHERE s.owner_username IN ({marks})", names,
                )
                if product_ids:
                    product_marks = ",".join(["%s"] * len(product_ids))
                    cursor.execute(f"DELETE FROM products WHERE id IN ({product_marks})", tuple(product_ids))
                cursor.execute(f"DELETE FROM stores WHERE owner_username IN ({marks})", names)
                cursor.execute(f"DELETE FROM users WHERE username IN ({marks})", names)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
