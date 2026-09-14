# SUT 三特性改造 + 接口测试展开 · 设计文档

> ⚠️ **已过时（历史快照）**：写于 2026-08-14 阶段 0。代码已于 2026-08-31 经历 MySQL 化 + RBAC 改造，存储（内存→MySQL）、防超卖（Lock→原子 UPDATE）、越权语义（403→404/403 分层）、ship 角色、secret_key 位置均已变化。仅作阶段 0 历史参考，勿当现行依据。现行状态见 `CLAUDE.md` + `docs/teaching-progress.md`。

> 日期：2026-08-14
> 项目：my-test-framework（电商全链路双测框架）
> 范围：阶段 0 —— SUT 核心改造
> 状态：待审阅（已按业界最佳实践自检修正）

## 1. 背景与目标

被测系统（SUT）当前是"够测但玩具"：纯内存存储、伪 token、下单直接变已支付。面试官追问两层就穿帮，测试也只能写直线用例。

**目标**：给 SUT 加上真实项目才有的特性，让测试能展开实际场景；同时保持"SUT 是靶子、不是产品"的定位——最小实现、够测即可。

三个选定的特性互相咬合成一条真实业务线：

```
登录(JWT) → 搜索 → 加购 → 下单(待支付+扣库存) → 支付(成功/拒绝/超时) → 状态流转 → 完成
```

## 2. 现状（改造前）

- 认证：`token-{username}-{n}` 伪 token，`?token=` 查询参数传递
- 订单：`POST /order` 下单直接 `status="paid"`，无中间状态
- 库存：下单即扣，无并发保护、无回补
- 无支付环节、无越权校验

## 3. 模块设计

### 3.1 JWT 鉴权

- 引入 `pyjwt` 依赖，`/login` 签发 JWT（payload 含 `username` + `exp`）
- **固定签名算法 `HS256`**（防止 `none`/算法混淆攻击），密钥 `SECRET_KEY` 放 `config/config.yaml`、不写进源码
- token 传递方式改为 `Authorization: Bearer <token>`（弃用 `?token=` 查询参数）
- 受保护接口统一校验签名 + 过期时间；签名错 / 过期 / 缺失 → 401
- `exp` 默认 30 分钟（业界交互应用建议 1-60 分钟），测试用短过期（如 1s）验证过期场景

### 3.2 订单状态机

状态：`PENDING(待支付) → PAID(已支付) → SHIPPED(已发货) → COMPLETED(已完成)`，外加 `CANCELED(已取消)` 终态

- 与淘宝官方订单状态对应（WAIT_BUYER_PAY → WAIT_SELLER_SEND_GOODS → WAIT_BUYER_CONFIRM_GOODS → TRADE_FINISHED）
- **迁移规则表驱动**（表驱动状态机），非法迁移（如 PENDING 直接 COMPLETED）→ 400
- 迁移规则：`PENDING → {PAID, CANCELED}`、`PAID → {SHIPPED}`、`SHIPPED → {COMPLETED}`
- 接口变更：
  - `POST /order`：生成 **PENDING** 订单（不再直接 paid）
  - `POST /order/{id}/pay`：支付，成功 PENDING → PAID；失败 PENDING → CANCELED
  - `POST /order/{id}/ship`：发货，PAID → SHIPPED
  - `POST /order/{id}/confirm`：确认收货，SHIPPED → COMPLETED
- 退款为**后续可选扩展**（阶段 1+），本次 CANCELED 只承接"支付失败取消"

### 3.3 模拟支付网关（故障注入）

- SUT 内置"第三方支付网关"模拟器，行为可切换 `success / fail / timeout`
- 切换方式：`POST /debug/payment-mode`（仅 `DEBUG_MODE=1` 时注册），测试 setup 切模式、teardown 恢复
- 支付结果分两类，语义不同（这是测试的关键）：
  - `success`：返回 200 `{"status": "paid"}`，订单 → PAID
  - `fail`（业务拒绝，如余额不足）：返回 200 `{"status": "failed", "reason": "..."}`，订单 PENDING → CANCELED 并**回补库存**——业务失败不该重试（订单已终态，重试被状态机拒绝）
  - `timeout`（系统故障）：挂起超过客户端超时 → 客户端抛超时——**系统故障才触发框架重试**

### 3.4 库存扣减与防超卖

- **下单时原子扣减库存**（生成 PENDING 订单时扣），支付失败/取消时**回补库存**
- 选择"下单扣"而非"支付扣"的原因：支付扣在并发下最易超卖（下单成功数远超库存）
- 防超卖：用 `threading.Lock` 保护"校验库存 + 扣减"临界区（对应生产环境的悲观锁思路；真实高并发用数据库乐观锁 `UPDATE ... WHERE stock >= N` 或 Redis Lua）
- 测试：并发下单不超卖；支付失败后库存回补

### 3.5 越权校验

- `pay / ship / confirm` 校验订单归属（`order.username == 当前用户`），否则 403
- 购物车、订单本就按 username 隔离，无需额外改造

## 4. 接口清单

| 方法 | 路径 | 变更 | 说明 |
|------|------|------|------|
| POST | `/login` | 改 | 返回 JWT（含 exp） |
| GET | `/products` `/products/{id}` | 不变 | 商品搜索/详情 |
| POST/GET/DELETE | `/cart` | 改 | header 鉴权 |
| POST | `/order` | 改 | 生成 PENDING 订单 + 扣库存 |
| POST | `/order/{id}/pay` | 新增 | 走支付网关（success/fail/timeout） |
| POST | `/order/{id}/ship` | 新增 | 发货 |
| POST | `/order/{id}/confirm` | 新增 | 确认收货 |
| GET | `/orders` | 改 | 订单列表（含状态） |
| POST | `/debug/payment-mode` | 新增 | 故障注入开关（仅 DEBUG） |

## 5. 测试展开（四组）

| 组 | 覆盖场景 |
|----|---------|
| 鉴权组 | 伪造 token / 过期 token / 缺失 header / 篡改 payload → 401 |
| 状态机组 | 合法流转逐步断言；非法迁移被拒；越权操作被拒 |
| 支付异常组 | fail（业务拒绝）→ 订单保持 PENDING + 库存回补 + 不重试；timeout（系统故障）→ 触发框架重试 |
| 并发组 | 并发下单不超卖（成功数 ≤ 库存，库存 ≥ 0） |

## 6. 错误处理规范

- 401：鉴权失败（无 token / 签名错 / 过期）
- 403：越权（操作他人订单）
- 400：非法状态迁移 / 业务错误（库存不足、购物车为空）
- 404：资源不存在
- 支付 fail 用 200 + 业务状态码（HTTP 成功但业务失败，与系统故障区分）
- 响应统一 `{"detail": "..."}`（FastAPI 默认）

## 7. 依赖变更

- 新增：`pyjwt`
- 其余不变（fastapi / uvicorn / requests / pytest / pyyaml）

## 8. 验证方式

1. 全链路验证脚本（requests）覆盖鉴权/状态/支付/并发四组场景
2. pytest 用例全绿
3. 手动 curl 走一遍完整状态流转

## 9. 不在本次范围（后续阶段）

- 数据持久化（SQLite）—— 阶段 1+ 可选
- 商品分页/排序/筛选 —— 阶段 1+ 可选
- 订单取消/退款/超时自动取消 —— 阶段 1+ 可选
- UI 模块（POM）—— 阶段 2
- CI / Allure —— 阶段 1/3
- 性能压测（locust）—— 阶段 3+ 可选
