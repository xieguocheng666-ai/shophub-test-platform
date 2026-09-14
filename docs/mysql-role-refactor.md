# SUT 改造：MySQL 化 + 角色区分 + 配置外置

> ✅ **已完成（历史快照）**：本变更记录对应的改造已于 2026-08-31 全部落地（29 passed）。本文档记录决策过程，供复盘；进度见 `docs/teaching-progress.md`。

> 变更记录 + 继续指南
> 日期：2026-08-31 → 2026-09-02 标记完成

## 一、为什么做（动机）

原 SUT 有三个"玩具感"来源，面试深挖会露馅：

1. 数据用 Python 写死（`USERS = {...}` 在 main.py 里），内存 dict 存储
2. 没做角色区分，`test_cannot_ship_others_order` 标"垂直越权"却名不副实（SUT 谁都能 ship）
3. `config.yaml` 把测试配置、测试账号、SUT 密钥混在一起，明文密码

## 二、技术决策（玩具 → 真实）

| 维度 | 原来（玩具） | 改成（真实） | 选型理由 |
|------|------------|-------------|---------|
| 数据库 | 内存 dict | **MySQL 8.0**（本机已装） | 面试问"数据库用的什么"要拿得出手 |
| 数据访问层 | 无 | **PyMySQL 原生 SQL** | 测开岗 SQL 是考察点，原生 SQL 能讲透 |
| 防超卖 | `threading.Lock`（单进程局限） | **单条原子 UPDATE + rowcount** | InnoDB 行锁引擎层保证，跨进程成立 |
| 角色 | 无 | **buyer / seller**，ship 需 seller | 让"垂直越权"测试名副其实 |
| 密码存储 | 明文 | **加盐 PBKDF2（hashlib）** | 堵"密码明文存"硬伤 |
| 敏感信息 | 明文在 yaml | **.env + .gitignore** | 密码/密钥不进 git |

### 防超卖核心 SQL（面试要能脱稿讲）

```sql
UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?;
-- cursor.rowcount == 1 → 扣减成功；== 0 → 库存不足
```

### 越权防护双层

```
水平越权（操作他人数据）：get_order(username, order_id)  WHERE 同时限定 username + order_id → 404
垂直越权（执行更高权限动作）：ship 接口 _require_seller 校验 role == seller → 403
```

角色从**数据库实时查**，不信任客户端 token（token 只带 username）。

## 三、数据库 schema（shop 库，5 张表）

```
users(username PK, password[hash], nickname, role ENUM buyer/seller)
products(id PK AI, name, price DECIMAL(10,2), stock INT)
carts(username+product_id 复合 PK, quantity)
orders(order_id PK AI, username, status ENUM, total, created_at)
order_items(id PK AI, order_id FK, product_id FK, name, price, quantity)
```

种子账号：alice/bob（buyer）、seller（seller），密码 alice123/bob123/seller123（存 hash）。

## 四、已完成（代码已落地）

- [x] 装 PyMySQL + python-dotenv，更新 requirements.txt
- [x] 建库建表 + 种子（`server/db.py` 的 `init_db()` 已执行，数据已入库）
- [x] `server/db.py`：密码 hash/verify、CRUD、原子扣库存、事务下单、`get_order_any`、`restore_stock`、`reset_data`
- [x] `server/main.py`：内存 dict → db.py；`_require_seller` 角色校验；ship 用 `get_order_any`
- [x] `conftest.py`：load_dotenv + secret_key 从环境变量注入
- [x] `config.yaml`：去掉 secret_key，加 seller 账号
- [x] `.env`（DB 连接 + SECRET_KEY）+ `.gitignore`
- [x] 适配测试：`test_order_full_flow` / `test_full_ecommerce_flow` 改 seller 发货；`test_cannot_ship_others_order` → `test_buyer_cannot_ship`（403）

## 五、完成状态（2026-09-02 补记）

8-31 当天列出的"未完成"，现已全部完成：

1. ✅ 重启服务（已做）
2. ✅ 跑全量测试 29 passed 全绿
3. ✅ 知识点沉淀：改为拆 5 篇（`docs/knowledge/11~15`），第 1 篇 `11-mysql.md` 已写
4. ✅ 更新进度表（teaching-progress.md + progress-tracker.md）

## 六、遗留注意点

- ✅ 并发测试（20 线程）在 MySQL 原子 UPDATE 下已重跑通过（29 passed 含并发防超卖）
- `server/main.py` 里 `_find_order` 缩进问题已随重写解决
- 前端静态页面（login.html/cart.html）尚未适配角色（阶段2 UI 模块再处理）
