# 数据库结构与数据流参考手册

> 被测系统 `shop` 库的完整数据模型（平台模式：多商家开店 + 按店铺拆单）。
> 配合 `docs/knowledge/11-mysql.md` 一起看。
> 本手册重点：**每张表的结构 + 真实实例数据 + 一次下单的数据流向**。

## 一、整体关系图（ER 图）

```
users（账号）                    products（商品 SPU，只有名字）
  │ 1:N（一个商家可开多店）          │ 1:N
  ▼                                ▼
stores（店铺）─── 1:N ──── store_product（店铺在售商品：价格/库存在这）
  │ 1:N                             ▲
  ▼                                │
carts（购物车，带店铺维度）          │（扣库存）
  │ 下单                            │
  ▼                                │
orders（父订单）──1:N──▶ sub_orders（子订单，按店铺拆）──1:N──▶ order_items（明细快照）
```

**箭头 = 外键（foreign key /ˈfɒrən kiː/）**：一列指向另一张表的主键，用来把两张表的数据关联起来。

核心关系：
- `stores.owner_username` → `users.username`（店铺归哪个商家，一个商家可开多店）
- `store_product.(store_id, product_id)` → `stores` + `products`（哪家店在卖哪个商品，价格库存挂这）
- `carts.(username, store_id, product_id)` → `users` + `stores` + `products`（谁加购了哪家店的哪个商品）
- `orders.username` → `users.username`（父订单是谁的）
- `sub_orders.parent_order_id` → `orders.order_id`（子订单属于哪个父订单）
- `sub_orders.store_id` → `stores.store_id`（子订单是哪个店铺的）
- `order_items.sub_order_id` → `sub_orders.sub_order_id`（明细挂在子订单下）

## 二、每张表详细结构 + 实例数据

### 1. `users` 用户表

| 列 | 类型 | 约束 | 含义 |
|----|------|------|------|
| username | VARCHAR(50) | **主键** PRIMARY KEY | 用户名，唯一 |
| password | VARCHAR(128) | NOT NULL | 密码的哈希（加盐 PBKDF2，不是明文） |
| nickname | VARCHAR(50) | NOT NULL | 昵称 |
| role | ENUM('buyer','seller') | NOT NULL，默认 buyer | 角色：买家/卖家 |

**实例数据（seed 后）：**

| username | nickname | role |
|----------|----------|------|
| alice | 爱丽丝 | buyer |
| bob | 鲍勃 | buyer |
| seller1 | 商家一 | seller |
| seller2 | 商家二 | seller |

> `ENUM` /ˈiːnəm/：只能取括号里列出的几个值之一，防止写错角色。

### 2. `stores` 店铺表

| 列 | 类型 | 约束 | 含义 |
|----|------|------|------|
| store_id | INT | **主键** + AUTO_INCREMENT | 店铺 id |
| owner_username | VARCHAR(50) | NOT NULL + 外键 | 店铺归哪个商家（**不加 UNIQUE，一个商家可开多店**） |
| store_name | VARCHAR(100) | NOT NULL | 店铺名 |

**实例数据（seed 后）：**

| store_id | owner_username | store_name |
|----------|---------------|-----------|
| 1 | seller1 | 联想官方旗舰店 |
| 2 | seller2 | 罗技专卖店 |

> 平台模式关键：店铺（stores）和账号（users）分离。账号是"登录身份"，店铺是"经营实体"。真实平台里商家要注册店铺才能卖货，一个商家可持有多店。

### 3. `products` 商品表（SPU 层）

| 列 | 类型 | 约束 | 含义 |
|----|------|------|------|
| id | INT | **主键** + AUTO_INCREMENT | 商品 SPU id |
| name | VARCHAR(100) | NOT NULL | 商品名 |

> **SPU /es-piː-juː/（Standard Product Unit，标准产品单元）**：商品的"通用信息"，不涉及谁卖、卖多少钱。价格库存全部下放到 `store_product`，因为同一个 SPU 会被多个店铺销售、各自定价备货。

**实例数据（seed 后）：**

| id | name |
|----|------|
| 1 | 联想小新 Pro16 笔记本 |
| 2 | 罗技 K845 机械键盘 |
| 3 | 罗技 G304 无线鼠标 |
| 4 | AOC 27 寸 2K 显示器 |
| 5 | 闪迪 64G USB3.1 U盘 |
| 6 | 品胜 Type-C 数据线 |
| 7 | 索尼 WH-1000XM4 降噪耳机 |
| 8 | 罗技 C920 高清摄像头 |

### 4. `store_product` 店铺在售商品表（中间表）

| 列 | 类型 | 约束 | 含义 |
|----|------|------|------|
| store_id | INT | **复合主键** + 外键 | 哪个店铺 |
| product_id | INT | **复合主键** + 外键 | 哪个商品 |
| price | DECIMAL(10,2) | NOT NULL | 该店铺的售价 |
| stock | INT | NOT NULL | 该店铺的库存 |

> **复合主键 `(store_id, product_id)`**：同一个商品可以被多家店卖，但"某家店 + 某商品"只有一行。价格库存挂在这里，是实现"同商品多店铺库存隔离"的关键。

**实例数据（seed 后，注意商品 1、2 两家店都在卖，价格库存不同）：**

| store_id | product_id | price | stock |
|----------|-----------|-------|-------|
| 1 | 1 | 5499.00 | 50 |
| 1 | 2 | 299.00 | 100 |
| 1 | 3 | 149.00 | 200 |
| 1 | 4 | 1099.00 | 30 |
| 2 | 1 | 5299.00 | 30 |
| 2 | 2 | 289.00 | 80 |
| 2 | 5 | 39.90 | 500 |
| 2 | 6 | 19.90 | 1000 |
| 2 | 7 | 1999.00 | 40 |
| 2 | 8 | 399.00 | 80 |

> `DECIMAL(10,2)` 存钱：精确小数，不用 FLOAT（会丢精度）。

### 5. `carts` 购物车表

| 列 | 类型 | 约束 | 含义 |
|----|------|------|------|
| username | VARCHAR(50) | **复合主键** + 外键 | 谁加购 |
| store_id | INT | **复合主键** + 外键 | 从哪家店买 |
| product_id | INT | **复合主键** + 外键 | 哪个商品 |
| quantity | INT | NOT NULL | 买几件 |

> **复合主键 `(username, store_id, product_id)` 三列**：同一个人从同一家店加购同一个商品，只有一行，重复加购只改 quantity。加店铺维度，是因为同一商品不同店铺价格库存不同，必须明确"从哪家店买"。

**实例数据（alice 加购 store1 的 2 件商品 1 之后）：**

| username | store_id | product_id | quantity |
|----------|----------|-----------|----------|
| alice | 1 | 1 | 2 |

### 6. `orders` 父订单表

| 列 | 类型 | 约束 | 含义 |
|----|------|------|------|
| order_id | INT | **主键** + AUTO_INCREMENT | 父订单号 |
| username | VARCHAR(50) | NOT NULL + 外键 | 谁的订单 |
| status | ENUM(...) | NOT NULL | 父订单状态（聚合） |
| total | DECIMAL(10,2) | NOT NULL | 订单总额（所有子订单之和） |
| created_at | DATETIME | 默认当前时间 | 下单时间 |

父订单 `status` 四个值（聚合态，不含 shipped）：

```
pending(待支付) ──支付成功──▶ paid(已支付) ──(所有子订单都完成)──▶ completed(已完成)
     │
     └────支付失败────▶ canceled(已取消)
```

> **父订单面向买家**：一次结算生成一个父订单，展示总金额和整体状态。它自己不拥有明细、不负责发货，只做"支付锚点 + 聚合"。

### 7. `sub_orders` 子订单表

| 列 | 类型 | 约束 | 含义 |
|----|------|------|------|
| sub_order_id | INT | **主键** + AUTO_INCREMENT | 子订单号 |
| parent_order_id | INT | NOT NULL + 外键 | 属于哪个父订单 |
| store_id | INT | NOT NULL + 外键 | 哪个店铺的子订单 |
| store_name | VARCHAR(100) | NOT NULL | **店铺名快照**（下单时刻） |
| status | ENUM(...) | NOT NULL | 子订单状态（独立流转） |
| total | DECIMAL(10,2) | NOT NULL | 该店铺部分的小计 |
| created_at | DATETIME | 默认当前时间 | 下单时间 |

子订单 `status` 五个值 + 状态机流转：

```
pending(待支付) ──支付成功──▶ paid(已支付) ──发货──▶ shipped(已发货) ──确认收货──▶ completed(已完成)
     │
     └────支付失败────▶ canceled(已取消)
```

> **子订单面向店铺/履约**：按店铺拆出来的独立履约单元，各自发货、各自流转状态。`store_name` 存快照，防止店铺改名后历史订单显示乱（和 order_items 存商品快照同理）。

### 8. `order_items` 订单明细表

| 列 | 类型 | 约束 | 含义 |
|----|------|------|------|
| id | INT | **主键** + AUTO_INCREMENT | 明细行 id |
| sub_order_id | INT | NOT NULL + 外键 | 属于哪个子订单 |
| product_id | INT | NOT NULL + 外键 | 哪个商品 |
| name | VARCHAR(100) | NOT NULL | 商品名（下单时快照） |
| price | DECIMAL(10,2) | NOT NULL | 下单时单价（快照） |
| quantity | INT | NOT NULL | 数量 |

> 为什么明细要存 `name` 和 `price` 的**快照（snapshot /ˈsnæpʃɒt/）**，而不是只存 product_id 再去查？因为商品以后可能改名/涨价，但历史订单必须记录「下单那一刻」的商品名和价格。明细挂在子订单下（不挂父订单），因为履约按子订单进行。

## 三、父子订单状态机

父订单和子订单是两层状态机，分工不同：

```
支付（父订单层，一次付清整个购物车）
    orders: pending ──────────▶ paid            （父订单）
    所有 sub_orders: pending ──▶ paid            （子订单同步）

发货 / 确认收货（子订单层，各自独立）
    sub_orders: paid ──发货──▶ shipped ──确认收货──▶ completed

父订单聚合完成
    orders: paid ──(所有子订单都 completed)──▶ completed

支付失败（父子一起取消 + 回补库存）
    orders + 所有 sub_orders: pending ──▶ canceled
```

关键点：**支付只发生一次（父订单层），发货/收货按子订单各自进行**。一个父订单拆成 N 个子订单后，每个子订单独立发货，全部完成后父订单才标记 completed。

## 四、数据流向：一次下单，数据怎么动

`create_order`（db.py）在一个事务里完成「按店铺拆单 + 原子扣库存 + 建父子订单 + 清购物车」：

```
① 读 carts（含店铺维度，JOIN store_product 拿价格、JOIN stores 拿店名）
② 原子扣库存     ──▶ UPDATE store_product SET stock = stock - qty WHERE store_id=? AND product_id=? AND stock>=qty
③ 建父订单        ──▶ INSERT orders（status=pending, total=全部）
④ 按 store 分组拆单 ──▶ 每个店铺 INSERT 一条 sub_orders（store_name 快照, total=该店铺小计）
⑤ 写明细          ──▶ 每个商品 INSERT 一条 order_items（挂在对应 sub_order 下，name/price 快照）
⑥ 清购物车        ──▶ DELETE carts（下单后购物车空了）
```

支付环节再动两张表：

| 动作 | 改哪张表 | 改什么 |
|------|---------|--------|
| 支付成功 | orders + sub_orders | 父订单 pending→paid，所有子订单 pending→paid |
| 支付失败 | store_product + orders + sub_orders | 回补库存 + 父子 → canceled |
| 发货（seller） | sub_orders | 该子订单 paid → shipped |
| 确认收货 | sub_orders（+ orders 聚合） | 子订单 shipped → completed，全完成后父订单 → completed |

## 五、功能 ↔ 表 对应关系

| 接口 | 读/写的表 |
|------|----------|
| `/login` | users（校验密码） |
| `/products`（搜索） | store_product + products + stores（JOIN） |
| `/store-products/{sid}/{pid}`（商品详情） | store_product + products + stores |
| `/cart`（加购/查/删） | carts + store_product（校验库存） |
| `/order`（下单） | carts + store_product + orders + sub_orders + order_items |
| `/order/{id}/pay`（支付） | orders + sub_orders（+ store_product 回补） |
| `/sub-orders/{id}/ship`（发货） | sub_orders |
| `/sub-orders/{id}/confirm`（确认收货） | sub_orders + orders（聚合） |
| `/stores`（注册/查店铺） | stores |
| `/stores/{id}/products`（上架/查） | store_product + products |
| `/stores/{id}/products/{pid}/stock`（改库存） | store_product |

## 六、越权矩阵（三层防护）

| 越权类型 | 场景 | 接口 | 防护 |
|---------|------|------|------|
| 垂直越权 | buyer 注册店铺/上架商品/发货 | `/stores`、`/stores/{id}/products`、`/sub-orders/{id}/ship` | `_require_seller` 校验 role → 403 |
| 水平越权·订单 | bob 支付 alice 的订单 | `/order/{id}/pay` | `get_order` WHERE username+order_id → 404 |
| 水平越权·店铺 | seller1 改 seller2 店铺库存 | `/stores/{id}/products/{pid}/stock` | `_check_store_owner` 校验 owner → 403 |
| 水平越权·子订单 | seller1 发 seller2 店铺的子订单 | `/sub-orders/{id}/ship` | 校验子订单 store_id 归属 → 403 |

## 七、完整实例：alice 跨店铺买 2 个笔记本 + 1 个 U盘

**下单前（alice 已加购两个店铺的商品）：**

| carts | | | |
|-------|--|--|--|
| username | store_id | product_id | quantity |
| alice | 1 | 1 | 2 |
| alice | 2 | 5 | 1 |

**下单后，各表变化：**

| store_product 变化 | | |
|-------------------|--|--|
| (store1, product1) 联想笔记本 | stock 50 → **48** |
| (store2, product5) 闪迪U盘 | stock 500 → **499** |

| orders 新增（父订单） | |
|----------------------|--|
| order_id | 1 |
| username | alice |
| status | pending |
| total | **11037.90**（5499×2 + 39.9×1） |

| sub_orders 新增（拆成 2 个子订单） | | | | |
|----------------------------------|--|--|--|--|
| sub_order_id | parent_order_id | store_id | store_name | total |
| 1 | 1 | 1 | 联想官方旗舰店 | 10998.00 |
| 2 | 1 | 2 | 罗技专卖店 | 39.90 |

| order_items 新增（挂在子订单下） | | | | | |
|-------------------------------|--|--|--|--|--|
| id | sub_order_id | product_id | name | price | quantity |
| 1 | 1 | 1 | 联想小新 Pro16 笔记本 | 5499.00 | 2 |
| 2 | 2 | 5 | 闪迪 64G USB3.1 U盘 | 39.90 | 1 |

| carts 变化 | |
|-----------|--|
| alice 的两行 | **被删空** |

**一句话总结数据流**：购物车（carts）是「暂存区」，下单把它按店铺分组，转成一个父订单（orders）+ 每个店铺一个子订单（sub_orders）+ 若干明细（order_items），同时扣掉各店铺在售商品的库存（store_product）。
