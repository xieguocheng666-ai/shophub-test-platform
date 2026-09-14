"""数据访问层：被测系统对 MySQL 的所有 SQL 操作。

核心对象：pymysql.Connection。每个操作独立建连、用完关闭——
FastAPI 把同步 handler 丢进线程池跑，共享连接会有线程安全问题，
所以每个操作各自建连，简单且线程安全。

数据库连接信息从环境变量读（.env 注入），不写死在代码里。
"""
import hashlib
import os
import secrets

import pymysql
from fastapi import HTTPException
from pymysql.cursors import DictCursor

# ---------- 种子数据（建库时插入，与真实电商初始数据一致） ----------
SEED_USERS = [
    ("alice", "alice123", "爱丽丝", "buyer"),
    ("bob", "bob123", "鲍勃", "buyer"),
    ("seller1", "seller123", "商家一", "seller"),
    ("seller2", "seller456", "商家二", "seller"),
]

# (store_id, owner_username, store_name) —— store_id 显式指定，便于 store_product 引用
SEED_STORES = [
    (1, "seller1", "联想官方旗舰店"),
    (2, "seller2", "罗技专卖店"),
]

# 商品是 SPU（只有名字），价格库存下放到 store_product
SEED_PRODUCTS = [
    ("联想小新 Pro16 笔记本",),
    ("罗技 K845 机械键盘",),
    ("罗技 G304 无线鼠标",),
    ("AOC 27 寸 2K 显示器",),
    ("闪迪 64G USB3.1 U盘",),
    ("品胜 Type-C 数据线",),
    ("索尼 WH-1000XM4 降噪耳机",),
    ("罗技 C920 高清摄像头",),
]

# (store_id, product_id, price, stock)
# 商品 1、2 两家店都在卖（价格不同）→ 测「同商品多店铺库存隔离」
SEED_STORE_PRODUCT = [
    # 联想官方旗舰店（store 1）
    (1, 1, 5499.0, 50),
    (1, 2, 299.0, 100),
    (1, 3, 149.0, 200),
    (1, 4, 1099.0, 30),
    # 罗技专卖店（store 2）
    (2, 1, 5299.0, 30),
    (2, 2, 289.0, 80),
    (2, 5, 39.9, 500),
    (2, 6, 19.9, 1000),
    (2, 7, 1999.0, 40),
    (2, 8, 399.0, 80),
]

DDL = [
    """CREATE TABLE IF NOT EXISTS users (
        username VARCHAR(50) PRIMARY KEY,
        password VARCHAR(128) NOT NULL,
        nickname VARCHAR(50) NOT NULL,
        role ENUM('buyer','seller') NOT NULL DEFAULT 'buyer'
    )""",
    """CREATE TABLE IF NOT EXISTS stores (
        store_id INT PRIMARY KEY AUTO_INCREMENT,
        owner_username VARCHAR(50) NOT NULL,
        store_name VARCHAR(100) NOT NULL,
        FOREIGN KEY (owner_username) REFERENCES users(username)
    )""",
    """CREATE TABLE IF NOT EXISTS products (
        id INT PRIMARY KEY AUTO_INCREMENT,
        name VARCHAR(100) NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS store_product (
        store_id INT NOT NULL,
        product_id INT NOT NULL,
        price DECIMAL(10,2) NOT NULL,
        stock INT NOT NULL,
        PRIMARY KEY (store_id, product_id),
        FOREIGN KEY (store_id) REFERENCES stores(store_id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    )""",
    """CREATE TABLE IF NOT EXISTS carts (
        username VARCHAR(50) NOT NULL,
        store_id INT NOT NULL,
        product_id INT NOT NULL,
        quantity INT NOT NULL,
        PRIMARY KEY (username, store_id, product_id),
        FOREIGN KEY (username) REFERENCES users(username),
        FOREIGN KEY (store_id) REFERENCES stores(store_id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    )""",
    """CREATE TABLE IF NOT EXISTS orders (
        order_id INT PRIMARY KEY AUTO_INCREMENT,
        username VARCHAR(50) NOT NULL,
        status ENUM('pending','paid','completed','canceled') NOT NULL,
        total DECIMAL(10,2) NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (username) REFERENCES users(username)
    )""",
    """CREATE TABLE IF NOT EXISTS sub_orders (
        sub_order_id INT PRIMARY KEY AUTO_INCREMENT,
        parent_order_id INT NOT NULL,
        store_id INT NOT NULL,
        store_name VARCHAR(100) NOT NULL,
        status ENUM('pending','paid','shipped','completed','canceled') NOT NULL,
        total DECIMAL(10,2) NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (parent_order_id) REFERENCES orders(order_id),
        FOREIGN KEY (store_id) REFERENCES stores(store_id)
    )""",
    """CREATE TABLE IF NOT EXISTS order_items (
        id INT PRIMARY KEY AUTO_INCREMENT,
        sub_order_id INT NOT NULL,
        product_id INT NOT NULL,
        name VARCHAR(100) NOT NULL,
        price DECIMAL(10,2) NOT NULL,
        quantity INT NOT NULL,
        FOREIGN KEY (sub_order_id) REFERENCES sub_orders(sub_order_id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    )""",
]


# ---------- 连接 ----------
def get_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "shop"),
        charset="utf8mb4",
        cursorclass=DictCursor,
    )


# ---------- 密码哈希（加盐 + PBKDF2，标准库 hashlib 实现） ----------
def hash_password(password: str) -> str:
    """加盐哈希，存成 "盐hex:哈希hex"。"""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)
    return salt.hex() + ":" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    salt_hex, dk_hex = stored.split(":")
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 100000)
    return dk.hex() == dk_hex


# ---------- 建库建表 + 种子 ----------
def init_db():
    """建库（无库连接）→ 建表 → 插种子。幂等，可重复执行。"""
    conn = pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS shop "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for stmt in DDL:
                cur.execute(stmt)
            _seed(cur)
        conn.commit()
    finally:
        conn.close()


def _seed(cur):
    cur.execute("SELECT COUNT(*) AS n FROM users")
    if cur.fetchone()["n"] == 0:
        for username, password, nickname, role in SEED_USERS:
            cur.execute(
                "INSERT INTO users (username, password, nickname, role) VALUES (%s,%s,%s,%s)",
                (username, hash_password(password), nickname, role),
            )
    cur.execute("SELECT COUNT(*) AS n FROM stores")
    if cur.fetchone()["n"] == 0:
        for store_id, owner_username, store_name in SEED_STORES:
            cur.execute(
                "INSERT INTO stores (store_id, owner_username, store_name) VALUES (%s,%s,%s)",
                (store_id, owner_username, store_name),
            )
    cur.execute("SELECT COUNT(*) AS n FROM products")
    if cur.fetchone()["n"] == 0:
        for (name,) in SEED_PRODUCTS:
            cur.execute("INSERT INTO products (name) VALUES (%s)", (name,))
    cur.execute("SELECT COUNT(*) AS n FROM store_product")
    if cur.fetchone()["n"] == 0:
        for store_id, product_id, price, stock in SEED_STORE_PRODUCT:
            cur.execute(
                "INSERT INTO store_product (store_id, product_id, price, stock) VALUES (%s,%s,%s,%s)",
                (store_id, product_id, price, stock),
            )


# ---------- 用户 ----------
def get_user(username: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username, password, nickname, role FROM users WHERE username=%s",
                (username,),
            )
            return cur.fetchone()
    finally:
        conn.close()


# ---------- 店铺 ----------
def get_store(store_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT store_id, owner_username, store_name FROM stores WHERE store_id=%s",
                (store_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "store_id": row["store_id"],
            "owner_username": row["owner_username"],
            "store_name": row["store_name"],
        }
    finally:
        conn.close()


def list_stores_by_owner(owner_username: str):
    """查某商家拥有的所有店铺（一个商家可开多店）。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT store_id, store_name FROM stores WHERE owner_username=%s ORDER BY store_id",
                (owner_username,),
            )
            rows = cur.fetchall()
        return [{"store_id": r["store_id"], "store_name": r["store_name"]} for r in rows]
    finally:
        conn.close()


def create_store(owner_username: str, store_name: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO stores (owner_username, store_name) VALUES (%s,%s)",
                (owner_username, store_name),
            )
            store_id = cur.lastrowid
        conn.commit()
        return {"store_id": store_id, "owner_username": owner_username, "store_name": store_name}
    finally:
        conn.close()


# ---------- 商品 ----------
def list_products(keyword: str = "", page: int = 1, page_size: int = 20):
    """买家看到的商品 = 店铺在售商品（含店铺维度），分页返回。

    同一 SPU 多店铺在售会各占一行；返回 {items, total, page, page_size}。
    """
    where = "WHERE p.name LIKE %s" if keyword else ""
    params = [f"%{keyword}%"] if keyword else []
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM store_product sp "
                "JOIN products p ON sp.product_id = p.id " + where,
                params,
            )
            total = cur.fetchone()["n"]
            cur.execute(
                "SELECT sp.store_id, s.store_name, sp.product_id, p.name, sp.price, sp.stock "
                "FROM store_product sp "
                "JOIN products p ON sp.product_id = p.id "
                "JOIN stores s ON sp.store_id = s.store_id " + where +
                " ORDER BY sp.store_id, sp.product_id LIMIT %s OFFSET %s",
                params + [page_size, (page - 1) * page_size],
            )
            rows = cur.fetchall()
        return {
            "items": [_product_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    finally:
        conn.close()


def get_store_product(store_id: int, product_id: int):
    """查某店铺的某个在售商品（含价格库存）。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sp.store_id, s.store_name, sp.product_id, p.name, sp.price, sp.stock "
                "FROM store_product sp "
                "JOIN products p ON sp.product_id = p.id "
                "JOIN stores s ON sp.store_id = s.store_id "
                "WHERE sp.store_id=%s AND sp.product_id=%s",
                (store_id, product_id),
            )
            row = cur.fetchone()
        return _product_dict(row) if row else None
    finally:
        conn.close()


def list_store_products(store_id: int):
    """查某店铺在售商品列表（seller 后台用）。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sp.store_id, s.store_name, sp.product_id, p.name, sp.price, sp.stock "
                "FROM store_product sp "
                "JOIN products p ON sp.product_id = p.id "
                "JOIN stores s ON sp.store_id = s.store_id "
                "WHERE sp.store_id=%s ORDER BY sp.product_id",
                (store_id,),
            )
            rows = cur.fetchall()
        return [_product_dict(r) for r in rows]
    finally:
        conn.close()


def _product_dict(row):
    return {
        "store_id": row["store_id"],
        "store_name": row["store_name"],
        "product_id": row["product_id"],
        "name": row["name"],
        "price": float(row["price"]),
        "stock": row["stock"],
    }


def add_store_product(store_id: int, name: str, price: float, stock: int):
    """上架商品：name 对应的 SPU 不存在则先创建，再挂到店铺（含价格库存）。"""
    conn = get_conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM products WHERE name=%s", (name,))
            row = cur.fetchone()
            if row:
                product_id = row["id"]
            else:
                cur.execute("INSERT INTO products (name) VALUES (%s)", (name,))
                product_id = cur.lastrowid
            cur.execute(
                "INSERT INTO store_product (store_id, product_id, price, stock) VALUES (%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE price=%s, stock=%s",
                (store_id, product_id, price, stock, price, stock),
            )
        conn.commit()
        return {
            "store_id": store_id,
            "product_id": product_id,
            "name": name,
            "price": float(price),
            "stock": stock,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_stock(store_id: int, product_id: int, stock: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE store_product SET stock=%s WHERE store_id=%s AND product_id=%s",
                (stock, store_id, product_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="店铺中没有该商品")
        conn.commit()
    finally:
        conn.close()


# ---------- 购物车 ----------
def cart_add(username: str, store_id: int, product_id: int, quantity: int):
    item = get_store_product(store_id, product_id)
    if not item:
        raise HTTPException(status_code=404, detail="商品不存在")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="数量必须大于 0")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT quantity FROM carts WHERE username=%s AND store_id=%s AND product_id=%s",
                (username, store_id, product_id),
            )
            row = cur.fetchone()
            new_qty = (row["quantity"] if row else 0) + quantity
            if new_qty > item["stock"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"{item['name']} 库存不足（仅剩 {item['stock']} 件）",
                )
            cur.execute(
                "INSERT INTO carts (username, store_id, product_id, quantity) VALUES (%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE quantity=%s",
                (username, store_id, product_id, quantity, new_qty),
            )
        conn.commit()
    finally:
        conn.close()
    return _cart_payload(username)


def cart_get(username: str):
    return _cart_payload(username)


def cart_remove(username: str, store_id: int, product_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM carts WHERE username=%s AND store_id=%s AND product_id=%s",
                (username, store_id, product_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="购物车中没有该商品")
        conn.commit()
    finally:
        conn.close()
    return _cart_payload(username)


def _cart_payload(username: str):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT c.store_id, s.store_name, c.product_id, p.name, sp.price, c.quantity "
                "FROM carts c "
                "JOIN stores s ON c.store_id = s.store_id "
                "JOIN products p ON c.product_id = p.id "
                "JOIN store_product sp ON c.store_id = sp.store_id AND c.product_id = sp.product_id "
                "WHERE c.username=%s "
                "ORDER BY c.store_id, c.product_id",
                (username,),
            )
            rows = cur.fetchall()
        items = []
        total = 0.0
        for r in rows:
            price = float(r["price"])
            subtotal = round(price * r["quantity"], 2)
            items.append(
                {
                    "store_id": r["store_id"],
                    "store_name": r["store_name"],
                    "product_id": r["product_id"],
                    "name": r["name"],
                    "price": price,
                    "quantity": r["quantity"],
                    "subtotal": subtotal,
                }
            )
            total += price * r["quantity"]
        return {"items": items, "total": round(total, 2)}
    finally:
        conn.close()


# ---------- 订单 ----------
def create_order(username: str):
    """下单：一个事务里完成「按店铺拆单 + 原子扣库存 + 建父子订单 + 清购物车」。"""
    conn = get_conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            # ① 读购物车（含店铺维度）
            cur.execute(
                "SELECT c.store_id, s.store_name, c.product_id, p.name, sp.price, c.quantity "
                "FROM carts c "
                "JOIN stores s ON c.store_id = s.store_id "
                "JOIN products p ON c.product_id = p.id "
                "JOIN store_product sp ON c.store_id = sp.store_id AND c.product_id = sp.product_id "
                "WHERE c.username=%s "
                "ORDER BY c.store_id, c.product_id",
                (username,),
            )
            items = cur.fetchall()
            if not items:
                raise HTTPException(status_code=400, detail="购物车为空，无法下单")
            for it in items:
                it["price"] = float(it["price"])  # DECIMAL → float，避免与 float 混算报错

            # ② 原子扣库存（每个店铺商品独立扣，WHERE 带 stock>=qty 防超卖）
            for it in items:
                cur.execute(
                    "UPDATE store_product SET stock = stock - %s "
                    "WHERE store_id=%s AND product_id=%s AND stock >= %s",
                    (it["quantity"], it["store_id"], it["product_id"], it["quantity"]),
                )
                if cur.rowcount == 0:
                    raise HTTPException(status_code=400, detail=f"{it['name']} 库存不足")

            # ③ 建父订单
            total = sum(it["price"] * it["quantity"] for it in items)
            cur.execute(
                "INSERT INTO orders (username, status, total) VALUES (%s,'pending',%s)",
                (username, total),
            )
            parent_order_id = cur.lastrowid

            # ④ 按店铺拆成子订单
            by_store = {}
            for it in items:
                key = it["store_id"]
                if key not in by_store:
                    by_store[key] = {"store_name": it["store_name"], "items": [], "total": 0.0}
                by_store[key]["items"].append(it)
                by_store[key]["total"] += it["price"] * it["quantity"]

            sub_orders = []
            for store_id in sorted(by_store):
                group = by_store[store_id]
                cur.execute(
                    "INSERT INTO sub_orders (parent_order_id, store_id, store_name, status, total) "
                    "VALUES (%s,%s,%s,'pending',%s)",
                    (parent_order_id, store_id, group["store_name"], round(group["total"], 2)),
                )
                sub_order_id = cur.lastrowid
                for it in group["items"]:
                    cur.execute(
                        "INSERT INTO order_items (sub_order_id, product_id, name, price, quantity) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (sub_order_id, it["product_id"], it["name"], it["price"], it["quantity"]),
                    )
                sub_orders.append(
                    {
                        "sub_order_id": sub_order_id,
                        "store_id": store_id,
                        "store_name": group["store_name"],
                        "total": round(group["total"], 2),
                    }
                )

            # ⑤ 清购物车
            cur.execute("DELETE FROM carts WHERE username=%s", (username,))

        conn.commit()
        return {
            "order_id": parent_order_id,
            "username": username,
            "total": round(total, 2),
            "status": "pending",
            "sub_orders": sub_orders,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_orders(username: str, page: int = 1, page_size: int = 20):
    """买家订单列表：父订单（不含子订单明细），分页返回 {orders, total, page, page_size}。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM orders WHERE username=%s", (username,))
            total = cur.fetchone()["n"]
            cur.execute(
                "SELECT order_id, username, status, total FROM orders WHERE username=%s "
                "ORDER BY order_id LIMIT %s OFFSET %s",
                (username, page_size, (page - 1) * page_size),
            )
            rows = cur.fetchall()
        return {
            "orders": [
                {
                    "order_id": r["order_id"],
                    "username": r["username"],
                    "status": r["status"],
                    "total": float(r["total"]),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    finally:
        conn.close()


def get_order(username: str, order_id: int):
    """只查「自己的」父订单——越权防护核心（WHERE 同时限定 username + order_id）。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT order_id, username, status, total FROM orders "
                "WHERE order_id=%s AND username=%s",
                (order_id, username),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "order_id": row["order_id"],
            "username": row["username"],
            "status": row["status"],
            "total": float(row["total"]),
        }
    finally:
        conn.close()


def get_parent_order(order_id: int):
    """按 order_id 查父订单（不限用户名，发货/聚合用）。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT order_id, username, status, total FROM orders WHERE order_id=%s",
                (order_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "order_id": row["order_id"],
            "username": row["username"],
            "status": row["status"],
            "total": float(row["total"]),
        }
    finally:
        conn.close()


def get_sub_order(sub_order_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sub_order_id, parent_order_id, store_id, store_name, status, total "
                "FROM sub_orders WHERE sub_order_id=%s",
                (sub_order_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "sub_order_id": row["sub_order_id"],
            "parent_order_id": row["parent_order_id"],
            "store_id": row["store_id"],
            "store_name": row["store_name"],
            "status": row["status"],
            "total": float(row["total"]),
        }
    finally:
        conn.close()


def get_order_detail(order_id: int):
    """父订单 + 子订单 + 明细的完整嵌套结构。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT order_id, username, status, total FROM orders WHERE order_id=%s",
                (order_id,),
            )
            parent = cur.fetchone()
            if not parent:
                return None
            cur.execute(
                "SELECT sub_order_id, store_id, store_name, status, total FROM sub_orders "
                "WHERE parent_order_id=%s ORDER BY sub_order_id",
                (order_id,),
            )
            subs = cur.fetchall()
        result = {
            "order_id": parent["order_id"],
            "username": parent["username"],
            "status": parent["status"],
            "total": float(parent["total"]),
            "sub_orders": [],
        }
        with conn.cursor() as cur:
            for s in subs:
                cur.execute(
                    "SELECT product_id, name, price, quantity FROM order_items WHERE sub_order_id=%s",
                    (s["sub_order_id"],),
                )
                rows = cur.fetchall()
                result["sub_orders"].append(
                    {
                        "sub_order_id": s["sub_order_id"],
                        "store_id": s["store_id"],
                        "store_name": s["store_name"],
                        "status": s["status"],
                        "total": float(s["total"]),
                        "items": [
                            {
                                "product_id": r["product_id"],
                                "name": r["name"],
                                "price": float(r["price"]),
                                "quantity": r["quantity"],
                            }
                            for r in rows
                        ],
                    }
                )
        return result
    finally:
        conn.close()


def pay_order(order_id: int):
    """支付：父订单 pending→paid，所有子订单同步 pending→paid。"""
    conn = get_conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE orders SET status='paid' WHERE order_id=%s AND status='pending'",
                (order_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=400, detail="订单状态不允许支付")
            cur.execute(
                "UPDATE sub_orders SET status='paid' WHERE parent_order_id=%s AND status='pending'",
                (order_id,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cancel_order_in_tx(cur, order_id: int) -> bool:
    """取消订单：回补库存 + 父订单/子订单 → canceled。

    幂等：先做「条件更新」抢占订单行（WHERE status='pending'），
    只有真的从 pending 改成 canceled 才继续回补库存，避免重复回补。
    返回是否真的取消了该订单。
    """
    cur.execute(
        "UPDATE orders SET status='canceled' WHERE order_id=%s AND status='pending'",
        (order_id,),
    )
    if cur.rowcount == 0:
        return False
    cur.execute(
        "UPDATE store_product sp "
        "JOIN sub_orders so ON sp.store_id = so.store_id "
        "JOIN order_items oi ON oi.sub_order_id = so.sub_order_id "
        "AND oi.product_id = sp.product_id "
        "SET sp.stock = sp.stock + oi.quantity "
        "WHERE so.parent_order_id = %s",
        (order_id,),
    )
    cur.execute(
        "UPDATE sub_orders SET status='canceled' WHERE parent_order_id=%s", (order_id,)
    )
    return True


def cancel_order(order_id: int):
    """支付失败：取消订单（同一事务）。"""
    conn = get_conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            _cancel_order_in_tx(cur, order_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cancel_expired_orders(timeout_seconds: int):
    """扫描超时未支付的 pending 订单并取消（Celery 周期任务的核心逻辑）。

    幂等：只处理 pending 且 created_at 超过 timeout 的订单，返回取消数量。
    """
    conn = get_conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT order_id FROM orders "
                "WHERE status='pending' AND created_at <= NOW() - INTERVAL %s SECOND",
                (int(timeout_seconds),),
            )
            rows = cur.fetchall()
            cancelled = sum(1 for r in rows if _cancel_order_in_tx(cur, r["order_id"]))
        conn.commit()
        return {"cancelled": cancelled}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ship_sub_order(sub_order_id: int):
    """发货：子订单 paid→shipped。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sub_orders SET status='shipped' WHERE sub_order_id=%s AND status='paid'",
                (sub_order_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=400, detail="子订单状态不允许发货")
        conn.commit()
    finally:
        conn.close()


def confirm_sub_order(sub_order_id: int):
    """确认收货：子订单 shipped→completed；所有子订单都 completed 时父订单→completed。

    并发安全：先 FOR UPDATE 锁父订单行，把同一父订单的并发确认串行化。
    否则两个并发事务各自用「快照读」COUNT，都读到旧快照里还有未完成子订单，
    谁都不更新父订单（REPEATABLE READ 写偏斜）。锁顺序统一「子订单 → 父订单」防死锁。
    """
    conn = get_conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sub_orders SET status='completed' WHERE sub_order_id=%s AND status='shipped'",
                (sub_order_id,),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=400, detail="子订单状态不允许确认收货")
            # FOR UPDATE 是当前读（不建立快照）；普通 SELECT 会建立快照，后续 COUNT 读旧数据
            cur.execute(
                "SELECT parent_order_id FROM sub_orders WHERE sub_order_id=%s FOR UPDATE",
                (sub_order_id,),
            )
            parent_id = cur.fetchone()["parent_order_id"]
            # 锁父订单行：同订单的并发确认会在这里排队，等前一个事务提交后才继续
            cur.execute(
                "SELECT order_id FROM orders WHERE order_id=%s FOR UPDATE", (parent_id,)
            )
            # 此时已持有父订单锁，事务的第一次快照读发生在锁获取之后，
            # 能看见前一个事务已提交的 completed，COUNT 结果正确
            cur.execute(
                "SELECT COUNT(*) AS n FROM sub_orders WHERE parent_order_id=%s AND status != 'completed'",
                (parent_id,),
            )
            if cur.fetchone()["n"] == 0:
                cur.execute("UPDATE orders SET status='completed' WHERE order_id=%s", (parent_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- 测试辅助 ----------
def reset_data():
    """清空所有表并重新插入种子，让每个测试从同一初始状态出发。"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for table in (
                "order_items", "sub_orders", "orders", "carts",
                "store_product", "products", "stores", "users",
            ):
                cur.execute(f"TRUNCATE TABLE {table}")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
            _seed(cur)
        conn.commit()
    finally:
        conn.close()
