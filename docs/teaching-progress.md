# my-test-framework 教学进度追踪

> 最后更新：2026-08-31
> 教学模式：**讲→验→做 + 知识点沉淀**（已封装为 skill `teach-test-framework`）

## 模式说明（讲→验→做）

每个 task 一个循环，5 步：

1. **讲**：我写代码 + 边写边讲（为什么需要 → 是什么 → 逐行解释）
2. **做**：留 1-N 个用例 / 小任务给你亲手写（ownership 留用户）
3. **沉淀**：把该 task 的知识点 + 你问过的问题总结成文档，放 `docs/knowledge/`
4. **更新**：在本表勾选进度，进下一个 task

> ⚠️ **提问不设时机**：你在「讲」和「做」的任何时刻都能随时打断提问，我停下当前进度先解答，答完再继续。你问过的问题最后统一沉淀进知识点文档。

**过关标准**：你能脱稿讲出这个概念，且你写的用例跑通。不是"代码对"。

## 阶段0：SUT 核心改造

> 代码已全部落地（pytest 15 passed）。现在用「讲→验→做」逐个概念带过。

| # | 概念 | 讲 | 做（用户写的用例） | 知识点文档 | 状态 |
|---|------|----|----|-----------|------|
| 1 | JWT 鉴权 | ✅ | `test_me_with_valid_token` | [01-jwt-auth.md](knowledge/01-jwt-auth.md) | ✅ 2026-08-15 |
| 2 | 订单状态机 | ✅ | — | 02-order-state-machine.md（未沉淀待补） | ✅ 已学 |
| 3 | 模拟支付网关（故障注入） | ✅ | ✅ test_pay_fail_restores_all_products_stock | [03-payment-gateway.md](knowledge/03-payment-gateway.md) | ✅ 2026-08-24 |
| 4 | 并发防超卖 | ✅ | ✅ test_concurrent_order_no_oversell_another | [04-concurrency.md](knowledge/04-concurrency.md) | ✅ 2026-08-25 |
| 5 | 越权校验 | ✅ | ✅ 亲手修复 `_find_order`（全局搜→只查自己）+ 断言 403→404 | [05-authz.md](knowledge/05-authz.md) | ✅ 2026-08-26 |

## 阶段1：接口测试深化

> 目标：全链路接口测试 + 数据驱动 + Allure + 并发竞态。并发竞态阶段0 已覆盖（2 个防超卖测试 + 04-concurrency.md），本阶段聚焦 Allure / 测试隔离 / 数据驱动深化 / 全链路补全。

| # | 概念 | 讲 | 做（用户写的） | 知识点文档 | 状态 |
|---|------|----|----|-----------|------|
| 1 | Allure 报告（feature/story） | ✅ | ✅ 给 13 个测试加 feature/story（"水平越权"归类判断正确） | [06-allure.md](knowledge/06-allure.md) | ✅ 2026-08-26 |
| 2 | 测试隔离（reset 接口 + autouse fixture） | ✅ | ✅ 跑两遍验证 + 脱稿讲 autouse | [07-test-isolation.md](knowledge/07-test-isolation.md) | ✅ 2026-08-28 |
| 3 | Allure 进阶（step/severity/attach） | ✅ | ✅ step 两种用法 + severity + attach（质疑纠正"冒烟测试该标 BLOCKER/CRITICAL 而非 MINOR"） | [08-allure-advanced.md](knowledge/08-allure-advanced.md) | ✅ 2026-08-28 |
| 4 | 数据驱动深化（parametrize 的 id / 数据外置 / 断言设计） | ✅ | ✅ 写 search 数据驱动（卡 yaml 嵌套，接手修正，4 passed） | [09-parametrize.md](knowledge/09-parametrize.md) | ✅ 2026-08-30 |
| 5 | 全链路测试补全 + 盲区排查 | ✅ | ✅ 用户写垂直越权 `test_cannot_ship_others_order`；购物车 5 个用例（含 cart_get/cart_remove 盲区）我补 | [10-e2e-and-coverage.md](knowledge/10-e2e-and-coverage.md) | ✅ 2026-08-31 |

## SUT 改造：MySQL 化 + 角色区分 + 配置外置（2026-08-31）

> 针对"玩具感"三问（config 简陋 / 数据写死在 Python / 没角色区分），把 SUT 从内存 dict 升级为真实 MySQL + RBAC。**代码已全部落地 + 全量测试 29 passed 全绿**（并发防超卖在 MySQL 原子 UPDATE 下重跑通过）。

### 改动清单（代码已落地）

| 改动 | 状态 |
|------|------|
| 数据访问层 `server/db.py`：PyMySQL 原生 SQL + 密码加盐 PBKDF2 + 原子扣库存 + 事务下单 | ✅ 代码落地 |
| `server/main.py`：内存 dict → db.py；`_require_seller` 角色校验；ship 用 `get_order_any` | ✅ 代码落地 |
| 配置外置：`.env`（DB 连接 + SECRET_KEY）+ `.gitignore` + conftest 注入 | ✅ 代码落地 |
| 测试适配：full_flow 改 seller 发货；`test_cannot_ship_others_order` → `test_buyer_cannot_ship`（403） | ✅ 代码落地 |
| 重启服务 + 跑全量测试（29 passed 全绿） | ✅ 2026-08-31 |

### 教学计划（5 节，讲→验→做，2026-09-01 制定）

> 知识点拆成 5 篇文档，每节对应一篇，避免一次性倾泻。顺序按概念依赖：第 1 节「数据在 MySQL」是整张地图的锚点 → 2/3（防超卖/RBAC 都建立在「数据在库」上）→ 4（密码哈希）→ 5（配置外置，收尾）。

| # | 概念 | 讲 | 做（用户亲手） | 知识点文档 | 状态 |
|---|------|----|----|-----------|------|
| 1 | 数据从内存 dict 到 MySQL（持久化 + PyMySQL + schema） | ✅ | ✅ 亲手查 products/users 表 + INSERT + JOIN 查购物车（乱码→字符集、JOIN、接口↔库链路都打通） | [11-mysql.md](knowledge/11-mysql.md) | ✅ 2026-09-01 |
| 2 | 防超卖：threading.Lock → 原子 UPDATE + rowcount | ⏸️ | 脱稿讲 rowcount 逻辑 + 写库存=1 边界用例 | [12-atomic-oversell.md](knowledge/12-atomic-oversell.md) | ⏸️ |
| 3 | RBAC 角色 + 水平/垂直越权 | ⏸️ | 写 buyer 调 ship 被 403 的测试 | [13-rbac.md](knowledge/13-rbac.md) | ⏸️ |
| 4 | 密码哈希 + 加盐 PBKDF2 | ⏸️ | 用 hashlib 手写 verify 对比 | [14-password-hash.md](knowledge/14-password-hash.md) | ⏸️ |
| 5 | 配置外置（.env/.gitignore） | ⏸️ | 检查 .gitignore 是否漏 .env | [15-config.md](knowledge/15-config.md) | ⏸️ |

> 变更记录 + 继续指南见 [mysql-role-refactor.md](mysql-role-refactor.md)。

---

## SUT 改造：平台模式重构（2026-09-02）

> 用户拍板：从「单店电商」升级为「平台电商」。讨论 A/B 方案（商品归属）、父订单 vs 按卖家拆单、店铺分离后定稿——**多商家多店 + 价格库存下放 + 父订单/子订单拆单 + 店铺名快照**。代码已全部落地 + 全量测试 **36 passed 全绿**。

### 表结构（5 张 → 8 张）

```
users → stores（一商家多店，1:N）→ store_product（价格/库存下放，复合主键）
orders（父订单）→ sub_orders（子订单，按店铺拆，store_name 快照）→ order_items（挂子订单）
carts（加 store 维度，三列复合主键）
```

### 改动清单（代码已落地）

| 改动 | 状态 |
|------|------|
| `server/db.py`：8 张表 DDL + 种子（2 seller / 2 店铺 / 商品 1、2 两店在售）+ 下单拆单事务 + 父子状态机 + 店铺 CRUD | ✅ |
| `server/main.py`：新增 `/stores` 系列接口 + 发货/确认改子订单层 + `_check_store_owner` 店铺级越权 | ✅ |
| 测试：适配 29 个 + 新增 7 个（越权矩阵 4 + 拆单/库存隔离 3），**36 passed 全绿** | ✅ |
| `docs/database-structure.md`：重写为 8 张表 + 拆单数据流 + 父子状态机 + 越权矩阵 | ✅ |

### 待教学知识点（讲→验→做，后续安排）

| 概念 | 说明 |
|------|------|
| 多对多中间表 + SPU/SKU 拆分 | 价格库存为何下放到 store_product，`(store_id, product_id)` 复合主键 |
| 父订单/子订单拆单 | 为什么要拆 + 父子两层状态机怎么分工 |
| 店铺与账号分离 | 1:N 关系 + 越权矩阵三层（订单/店铺/子订单） |

> 详细结构见 [database-structure.md](database-structure.md)。

---

## 2026-08-28 代码评估结论（对标行业标准）

> 阶段1 在自身目标上正常推进，Allure + 测试隔离已达标。评估发现的"并发隐患"和"测试盲区"**不是阶段1 欠的债，是后续阶段的活**，按渐进式标准安排：

| 发现 | 归类 | 落实安排 |
|------|------|---------|
| 并发边界（threading.Lock 单进程局限、order_id 锁外自增） | 后续阶段 | **阶段3 收尾/模拟面试**讲清楚边界即可，不改代码 |
| 购物车 / 搜索接口无测试 | 测试盲区 | **Task 4/5** 补测试用例 |
| 垂直越权无用例（ship/confirm 靠 `_find_order` 保证但无测试锁定） | 测试盲区 | **Task 5** 补 `test_cannot_ship_others_order` |
| `_find_order` 缩进 6 空格 | 代码格式 | 下次动到 `server/main.py` 时顺手改 4 空格 |

---

## 后续阶段：容器化 + 异步调度（2026-09-02 纳入计划）

> 用户拍板把两个新学点写进电商平台后续计划（源自方向讨论的折中结论——电商不放弃，Docker/Celery 作为电商项目后续深化方向，挂阶段 3 之后、投递后持续迭代）。

| 概念 | 说明 | 状态 |
|------|------|------|
| Docker 容器跑测试 | 环境隔离：CI 用 Docker 容器跑 pytest，消除"本机能跑、CI 跑不了"的环境差异 | ⬜ |
| Celery + Redis 异步调度 | 测试任务进队列异步执行（模块并行跑 / 定时跑），异步任务调度 | ⬜ |

> 详细里程碑见 CLAUDE.md「四、里程碑」阶段 4。

---

## 前端重写完成（2026-09-02）

> 独立把电商平台前端写完（放下 AI 测试智能体），以 8 表平台后端为准重写旧前端（旧版单店、`?token=` query 鉴权、缺 store_id 已全部废弃）。

### 交付清单

| 文件 | 说明 |
|------|------|
| `server/static/css/style.css` | 设计系统「精密货架」：墨蓝工业风 + 电光青 + 琥珀点缀 + 库存刻度条签名元素 |
| `server/static/js/api.js` | token 管理（localStorage）+ `Authorization: Bearer` header + fetch 封装 + 全部业务方法 |
| `server/static/login.html` | 登录（buyer/seller 都能登，演示账号点击自动填） |
| `server/static/index.html` | 商品列表（搜索 + 分页 + 店铺维度 + 库存刻度条 + 加购） |
| `server/static/cart.html` | 购物车（按店铺分组 + 删除 + 下单） |
| `server/static/orders.html` | 订单（父订单列表 + 详情弹窗：父子订单 + 支付/确认收货） |
| `server/static/seller.html` | 卖家后台（开店 + 上架 + 改库存 + 发货控制台） |

### 验证

- 后端改动仅 1 处：`/me` 返回 `role`（前端路由/展示需要，向后兼容）→ **36 passed 全绿**
- 浏览器全链路验证通过：登录 → 加购（跨店铺）→ 下单拆 2 子单 → 支付 → 发货 → 确认收货

### 暴露的后端问题（已修复 / 待决策）

| 问题 | 说明 | 状态 |
|------|------|------|
| 父订单完成状态并发丢失 | 并发确认多个子订单收货时，`confirm_sub_order` 的 `SELECT COUNT`（快照读）在两个事务里都读到「有子订单未完成」，父订单 stuck 在 paid（REPEATABLE READ 写偏斜）。顺序操作正常。 | ✅ 已修复：`confirm_sub_order` 加 `SELECT ... FOR UPDATE` 悲观锁（先锁子订单→父订单，统一锁序防死锁），并发确认串行化。回归测试 `test_concurrent_confirm_parent_completed` 已锁定。知识点沉淀见 [16-write-skew.md](knowledge/16-write-skew.md)。 |
| 卖家缺「待发货子订单列表」接口 | seller 后台发货需手动输 sub_order_id，后端无店铺维度子订单列表接口 | ⏸️ 待决策 |
