# 11 · 数据从内存 dict 到 MySQL

> 对应教学进度第 1 节。本节解决的困惑（用户原话）：
> "我对数据库的认识还停留在写 SQL 增删改查，不知道数据库是怎么跟接口建立联系的。"

## 一、一句话

把数据从「Python 内存变量」搬到「MySQL 数据库」，是被测系统从玩具变真实的第一步。

## 二、为什么：内存 dict 的三个短板

| 短板 | 后果 |
|------|------|
| 进程重启数据归零 | 数据不**持久化**（persistent /pərˈsɪstənt/，写磁盘，进程死了数据还在） |
| 只有单进程能共享 | 跨进程就没法防超卖 |
| 没法用 SQL 查 | 缺了「数据库」这个真实后端的核心角色 |

## 三、接口 ↔ 数据库 的完整链路（核心图）

```
浏览器 / 测试代码
      │ ① 发 HTTP 请求（比如 GET /products）
      ▼
FastAPI 接口函数          ← main.py 里的 @app.get("/products")
      │ ② 调用 db.list_products()
      ▼
db.py 里的函数            ← 唯一会发 SQL 的地方（数据访问层）
      │ ③ 用 PyMySQL 发一句 SQL 给 MySQL
      ▼
MySQL 数据库              ← shop 库的 products 表
      │ ④ 执行 SQL，把结果返回
      ▼
结果一层层往回传 → FastAPI 包成 JSON → 返回给浏览器
```

**核心**：接口函数（main.py）从不碰数据库，只会「喊」db.py 帮忙；db.py 才是唯一用 PyMySQL 发 SQL 的文件。

## 四、PyMySQL 的两个核心对象

| 对象 | 作用 | 类比 |
|------|------|------|
| `Connection`（连接）| 一条到 MySQL 的 TCP 通道 | 电话拨通的那根线 |
| `Cursor`（游标）| 在连接上发 SQL、取结果 | 电话里的话筒 |

**cursor** /ˈkɜːrsər/，帮你执行 SQL 并拿到结果的手柄。

## 五、db.py 一个函数走一遍（get_user，db.py:153）

```python
def get_user(username):
    conn = get_conn()               # ① 连上 MySQL（电话拨通）
    try:
        with conn.cursor() as cur:  # ② 拿一个游标（拿起话筒）
            cur.execute("...", (username,))  # ③ 发 SQL（对着话筒说话）
            return cur.fetchone()   # ④ 拿结果（听对方回答）
    finally:
        conn.close()                # ⑤ 挂断电话
```

`%s` 是 SQL 的**参数占位符**（PyMySQL 安全填充，防 SQL 注入），不是 Python 的 `%` 格式化。

## 六、5 张表 schema

```
shop 库
├── users        (username 主键, password, nickname, role)
├── products     (id 自增主键, name, price, stock)
├── carts        (username + product_id 复合主键, quantity)
├── orders       (order_id 自增主键, username, status, total)
└── order_items  (id, order_id, product_id, name, price, quantity)
```

`carts`/`orders` 里的 `username` 是**外键**，指向 users 表——把"购物车/订单是谁的"关联起来。

## 七、字符集乱码坑（真·工程常识）

- 数据用 `utf8mb4` 存，但 Windows 的 mysql 客户端默认用 `gbk` 解码 → 中文变方块。
- 解决：连接时加 `--default-character-set=utf8mb4`；对应 db.py 里 `charset="utf8mb4"`（db.py:85）。
- 判断线索：**数字没乱、只有中文乱** → 就是字符集问题，不是数据坏了。

## 八、JOIN /dʒɔɪn/

两张表「按相同的列对齐拼起来」。carts 只存 product_id 和数量，没有商品名；products 才有 name。要查「购物车里的商品叫什么」就得 JOIN：

```sql
SELECT c.product_id, p.name, p.price, c.quantity
FROM carts c JOIN products p ON c.product_id = p.id
WHERE c.username = 'alice';
```

这正是 db.py 里 `cart_get`（db.py:263）用的 SQL。

## 九、遗留（推迟到第 2 节）

线程 / 线程池 / 共享连接——和第 2 节「并发防超卖」是一件事的两面，讲并发时再回来，现在不用懂。
