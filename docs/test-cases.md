# 接口测试用例说明（ShopHub）

> 这份文档用“人话”说明 `api/tests/` 下每个用例：**测什么、前置条件、怎么操作、校验了什么**。
> 面向第一次接触这个项目的人，不需要先读代码。
> UI 用例见 `ui/test_ui.py`，性能场景见 `perf/`，不在本文范围。

## 〇、先认识几个词

| 词 | 人话解释 |
|---|---|
| 被测系统（SUT） | 我们写的那个假商城后端，测试就是拿它开刀 |
| 状态码 | HTTP 响应里的数字，200 成功、400 请求有问题、401 没登录、403 没权限、404 找不到 |
| detail | 出错时响应体里的错误说明，比如 `{"detail": "库存不足"}` |
| 越权 | 没权限的人去操作别人的东西。**垂直越权** = 买家干卖家的事；**水平越权** = 用户 A 动用户 B 的数据 |
| 幂等 | 同一个操作执行多次，效果和执行一次一样。比如“关单”跑两遍，库存只回补一次 |
| 拆单 | 一次结算买了两个店铺的东西，系统拆成两个子订单，各自发货 |
| 落库 | 数据真的写进了数据库，而不只是接口返回成功 |

## 一、每个用例开始前都会做什么

`conftest.py` 里有一个自动执行的 `reset_data`，**每条用例跑之前**都会：

1. 调用被测系统的 `/debug/reset`；
2. 把用户、店铺、商品、购物车、订单等表清空，重新插入固定种子数据；
3. 把支付模式恢复为 `success`。

所以每条用例都从同一份干净数据出发，互不影响。种子数据固定为：

- 买家：`alice` / `alice123`，`bob` / `bob123`
- 卖家：`seller1` / `seller123`（店铺 1），`seller2` / `seller456`（店铺 2）
- 商品 1~8，分布见 `docs/database-structure.md`

**通用启动前提**：被测系统必须以 `DEBUG_MODE=1` 启动，否则 `/debug/*` 路由不存在，重置会失败。

## 二、断言分四层，不是只看状态码

| 层 | 断什么 | 例子 |
|---|---|---|
| 1. HTTP 状态码 | 请求成功/失败的大方向 | 200、400、403 |
| 2. 响应体 | 成功看业务字段，失败看 `detail` 原因 | `status == "paid"`、`detail` 含“库存不足” |
| 3. 接口回读 | 换个接口再查一遍 | 下单后再查商品详情看库存 |
| 4. 直连数据库 | 数据是否真的落库、有没有副作用 | 查 `orders.status`、`store_product.stock` |

第 4 层用 `api/common/db_util.py` 直连 MySQL，**故意不复用被测代码的 `server/db.py`**。
原因：如果读写都走被测代码，写错了、读也以同样方式错，接口一来自查会“自洽地”通过；直连库才是独立信源。

数据库不可达时，用到第 4 层的用例会自动 skip（跳过），不会假失败。

---

## 三、基础与鉴权（`test_auth.py`，14 条）

| 用例 | 测什么（人话） | 前置条件 | 怎么操作 | 校验了什么 |
|---|---|---|---|---|
| `test_health` | 服务活着吗 | SUT 已启动 | GET `/health` | 200，响应体恰好是 `{"status": "ok"}` |
| `test_login` / 合法登录 | 正确账号能登录 | 无 | POST `/login`，alice/alice123 | 200，返回非空 `token` |
| `test_login` / 密码错误 | 密码错不能登录 | 无 | POST `/login`，alice/wrong-pass | 401，`detail` 含“用户名或密码错误” |
| `test_login` / 用户不存在 | 不存在的账号不能登录 | 无 | POST `/login`，nobody/whatever | 401，`detail` 含“用户名或密码错误” |
| `test_search_products` / 匹配多个 | 关键词能搜到多个商品 | 无 | GET `/products?keyword=罗技` | 返回 4 条，且每条商品名都含“罗技” |
| `test_search_products` / 匹配单个 | 关键词只命中一个 | 无 | keyword=索尼 | 返回 1 条 |
| `test_search_products` / 空关键词 | 不传关键词返回全部 | 无 | keyword 为空 | 返回 10 条（种子商品数） |
| `test_search_products` / 无结果 | 搜不到时返回空列表 | 无 | keyword=巧乐兹 | 返回 0 条 |
| `test_me_without_token` | 没带 token 不能访问个人信息 | 无 | GET `/me`，不带 Authorization | 401，`detail` 含“缺少 token” |
| `test_me_with_forged_token` | 瞎编的 token 不能用 | 无 | GET `/me`，Bearer `forged.token` | 401，`detail` 含“token 无效” |
| `test_me_with_wrong_signature` | 签名不对的 token 不能用 | 无 | 用错误密钥自签一个 JWT 再访问 `/me` | 401，`detail` 含“token 无效” |
| `test_me_with_expired_token` | 过期 token 不能用 | 无 | 用正确密钥签一个已过期 JWT 再访问 `/me` | 401，`detail` 含“token 已过期” |
| `test_me_with_valid_token` | 合法 token 能拿到自己的信息 | 已登录 alice | GET `/me`，带登录拿到的 token | 200，`username=alice`，`nickname=爱丽丝` |
| `test_password_stored_hashed` | 密码不是明文存库 | 数据库可访问 | 直接查 `users` 表 alice 的 `password` | 不等于 `alice123`；格式为“盐:哈希”（32 位 + 64 位 hex）；全表无明文密码 |

> 为什么登录失败用例只认“用户名或密码错误”：SUT 出于安全考虑，不区分“用户不存在”和“密码错”，避免暴露账号是否存在。测试按实际契约断言。

## 四、购物车（`test_cart.py`，5 条）

| 用例 | 测什么（人话） | 前置条件 | 怎么操作 | 校验了什么 |
|---|---|---|---|---|
| `test_cart_add_get_remove` | 加购、查看、移除能正常走通 | 已登录 alice | 加购店铺 1 商品 1 共 2 件 → GET `/cart` → DELETE `/cart/1/1` | 加购后接口返回 1 条且数量 2，**数据库 `carts` 行数量为 2**；移除后接口为空，**数据库该行真被删除（0 行）** |
| `test_cart_add_boundary` / 库存不足 | 买超过库存要被拒 | 已登录 alice | 加购店铺 1 商品 4 共 999 件（库存 30） | 400，`detail` 含“库存不足”；**数据库购物车仍为 0 行** |
| `test_cart_add_boundary` / 数量非法 | 数量 0 要被拒 | 已登录 alice | 加购 0 件 | 400，`detail` 含“数量必须大于 0”；**数据库购物车仍为 0 行** |
| `test_cart_add_boundary` / 商品不存在 | 不存在的商品不能加购 | 已登录 alice | 加购店铺 1 商品 9999 | 404，`detail` 含“商品不存在”；**数据库购物车仍为 0 行** |
| `test_order_empty_cart_rejected` | 空购物车不能下单 | 已登录 alice，购物车为空 | POST `/order` | 400，`detail` 含“购物车为空”；**数据库没有生成任何订单** |

> 失败用例最容易漏的是“副作用”：返回 400 只说明被拒了，还要证明**没有留下脏数据**，所以都补了数据库断言。

## 五、订单与支付（`test_order.py`，9 条）

| 用例 | 测什么（人话） | 前置条件 | 怎么操作 | 校验了什么 |
|---|---|---|---|---|
| `test_order_full_flow` | 一单从下单到收货的完整状态流转 | 已登录 alice | 加购店铺 1 商品 2 共 1 件 → 下单 → 支付 → seller1 发货 → alice 确认收货 | 下单 `pending`、支付 `paid`、发货 `shipped`、收货 `completed`；**数据库父订单 `completed`、没有未完成子订单、明细数量合计 1** |
| `test_illegal_transition_rejected` | 待支付订单不能直接确认收货 | 已登录 alice | 加购下单后，不支付直接 POST 确认收货 | 400，`detail` 含“不允许从 pending 变为 completed”；**数据库子订单仍是 `pending`** |
| `test_cannot_operate_others_order` | 不能支付别人的订单（水平越权） | alice 先下单，再用 bob 登录 | bob 调用 alice 订单的支付接口 | 404，`detail` 含“订单不存在”；**数据库该订单仍是 `pending`，没被支付** |
| `test_buyer_cannot_ship` | 买家不能发货（垂直越权） | alice 已下单，仍是买家身份 | alice 调用发货接口 | 403，`detail` 含“需要商家权限”；**数据库子订单仍是 `pending`** |
| `test_pay_fail_cancels_and_restores_stock` | 支付失败要取消订单并把库存加回来 | 已登录 alice，支付模式设为 fail | 记录库存 → 加购店铺 2 商品 5 共 2 件 → 下单 → 支付 | 支付返回 `failed`；订单列表最新一条 `canceled`；库存回到下单前；**数据库父订单 `canceled`、库存已回补、失败订单的明细仍保留（数量合计 2）** |
| `test_pay_fail_then_repay_rejected` | 已取消的订单不能再支付 | 已登录 alice | fail 模式下下单并支付（订单被取消）→ 切回 success 再支付一次 | 400，`detail` 含“订单状态不允许支付”；**数据库订单仍 `canceled`、库存没有被二次回补** |
| `test_pay_timeout_triggers_retry` | 支付网关超时时客户端要感知、订单不能乱动 | 支付模式设为 timeout | 用 1 秒超时的客户端加购下单并支付 | 客户端抛出 `requests.Timeout`；**数据库订单仍 `pending`，库存保持已扣减（下单扣了 1 件，超时不回补）** |
| `test_pay_fail_restores_all_products_stock` | 一单买多个商品，支付失败要全部回补 | 已登录 alice，支付模式 fail | 记录商品 5、6 库存 → 加购 5×2、6×1 → 下单 → 支付 | 支付 `failed`；接口查两商品库存都回到原值；**数据库两行库存都已回补** |
| `test_cancel_expired_idempotent` | 超时关单重复触发只生效一次 | 已登录 alice | 加购商品 2 共 2 件 → 下单 → 连续调用两次“取消超时订单” | 第一次返回 `cancelled=1`，第二次 `cancelled=0`；**数据库库存正好回补一次、订单 `canceled`、1 个子订单同步取消** |

> 幂等测试是这个项目最值钱的用例之一：关单逻辑如果重复执行，库存会被越补越多。接口返回 `cancelled=0` 只是表象，数据库里的库存数字才是铁证。

## 六、店铺越权、拆单与库存隔离（`test_store.py`，7 条）

| 用例 | 测什么（人话） | 前置条件 | 怎么操作 | 校验了什么 |
|---|---|---|---|---|
| `test_buyer_cannot_create_store` | 买家不能开店（垂直越权） | 已登录 alice（buyer） | POST `/stores` 开店“alice 的小店” | 403，`detail` 含“需要商家权限”；**数据库 `stores` 数量没变** |
| `test_buyer_cannot_add_product` | 买家不能上架商品（垂直越权） | 已登录 alice | POST 给店铺 1 上架“新商品” | 403，`detail` 含“需要商家权限”；**数据库 `products` 和 `store_product` 行数都没变** |
| `test_seller_cannot_operate_others_store` | 卖家不能改别人店铺的库存（水平越权） | seller1 已登录 | PATCH 把 seller2 店铺 2 商品 1 的库存改成 100 | 403，`detail` 含“无权操作他人店铺”；**数据库该商品库存没变** |
| `test_seller_cannot_ship_others_sub_order` | 卖家不能发别人店铺的子订单（水平越权） | alice 已下单并支付，seller2 已登录 | POST 让 seller2 发店铺 1 的子订单 | 403，`detail` 含“无权操作他人店铺”；**数据库子订单仍是 `paid`，没被发货** |
| `test_order_split_by_store` | 跨店下单要按店铺拆单，金额对得上 | 已登录 alice | 同时加购店铺 1 商品 1、店铺 2 商品 5 → 下单 | 接口返回 2 个子订单、店铺为 {1,2}、父总额=子单之和；**数据库：2 行子订单；明细分别挂到正确店铺的子订单（1,1,1）和（2,5,1）；父总额=明细金额之和；购物车清空；两店库存各 -1** |
| `test_same_product_multi_store_stock_isolation` | 同一商品在不同店铺的库存互不影响 | 已登录 alice | 记录两店商品 1 库存 → 从店铺 1 买 1 件 | 店铺 1 库存 -1，店铺 2 库存不变（接口 + **数据库双重确认**） |
| `test_cross_store_pay_fail_restores_all` | 跨店订单支付失败，两家店的库存都要回补 | 已登录 alice，支付模式 fail | 加购店铺 1 商品 1、店铺 2 商品 5 → 下单 → 支付 | 两店库存都回原值；**数据库父订单 `canceled`、两店库存都回补** |

> 拆单用例的数据库断言重点不是“接口返回了几条”，而是**明细有没有挂错子订单**、**父订单总额和明细金额是否一致**。这类错误接口层可能算得“看起来对”，但落库结构是错的。

## 七、端到端（`test_e2e.py`，2 条）

| 用例 | 测什么（人话） | 前置条件 | 怎么操作 | 校验了什么 |
|---|---|---|---|---|
| `test_full_ecommerce_flow` | 从搜索到收货的整条业务链 | 已登录 alice | 搜“罗技” → 取第一条加购 → 查看购物车 → 下单 → 支付 → seller1 发货 → alice 收货 | 每一步接口成功；**数据库最终父订单 `completed`、所有子订单完成、明细保存了商品名快照和数量、购物车清空** |
| `test_cancel_expired_orders` | 超时未支付订单会被自动取消并回补库存 | 已登录 alice | 记录库存 → 加购店铺 1 商品 2 → 下单（不支付）→ 调 `/debug/cancel-expired?seconds=0` | 返回 `cancelled=1`；订单列表最新一条 `canceled`；库存回原值；**数据库订单 `canceled`、库存已回补** |

## 八、并发（`test_concurrency.py`，3 条）

| 用例 | 测什么（人话） | 前置条件 | 怎么操作 | 校验了什么 |
|---|---|---|---|---|
| `test_concurrent_order_no_oversell` | 20 个线程抢商品 3，不能超卖 | bob 已登录，商品 3 有初始库存 | 记录初始库存 → 20 线程并发“加购 + 下单”，总请求数=库存+30 | 成功下单数 ≤ 初始库存；最终库存 ≥ 0；**数据库明细里的购买总量 = 初始库存 − 最终库存，且不超过初始库存** |
| `test_concurrent_order_no_oversell_another` | 换商品 4 再验一次防超卖 | bob 已登录 | 同上，商品 4 | 同上 |
| `test_concurrent_confirm_parent_completed` | 两个子订单同时确认收货，父订单要正确完成 | alice 跨店下单并支付，两店都已发货 | 两个线程同时确认两个子订单 | 两个请求都 200；接口查父订单 `completed`；**数据库父订单 `completed`、没有未完成子订单** |

> 防超卖的数据库断言逻辑：每卖出 1 件，`order_items` 就多 1 条数量记录，同时 `store_product.stock` 少 1。如果两者对不上，说明有“扣了库存但没生成订单”或“生成了订单但没扣库存”的漏洞，光看接口返回是发现不了的。

---

## 九、怎么跑

```bash
# 1. 起被测系统（必须带 DEBUG_MODE=1）
DEBUG_MODE=1 .venv/Scripts/python -m uvicorn server.main:app --reload

# 2. 跑全部接口用例
.venv/Scripts/python -m pytest api -v

# 3. 生成 Allure 报告
.venv/Scripts/python -m pytest api --alluredir=reports && allure serve reports
```

数据库连接信息从 `.env` 的 `DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME` 读取；
Docker 环境里 `DB_HOST=db` 由 `docker-compose.yml` 注入。
