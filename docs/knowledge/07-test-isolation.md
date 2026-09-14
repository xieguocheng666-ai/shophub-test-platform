# 07 · 测试隔离（数据清理）

> 阶段1 接口测试深化 · Task 2
> 日期：2026-08-26

## 问题

测试**无法重复运行**：第一遍 18 passed，第二遍 `test_illegal_transition_rejected` 报 `KeyError: 'order_id'`。

## 根因（完整链路）

```
第一次跑：并发测试 test_concurrent_order_no_oversell 对 product 3 下 230 单
          → 库存 200 → 0（永久改变，服务器内存没重启）

第二次跑：test_illegal_transition_rejected 想 cart_add(3, 1)
          → 库存为 0 → 加购 400 "库存不足"
          → 购物车空 → order_create 400 "购物车为空"
          → .json()["order_id"] 直接 KeyError
```

本质：**测试之间共享了服务器内存状态，且并发测试把库存耗光后不恢复**。

## 业界标准做法

被测系统提供**测试专用 reset 接口** + 测试框架用 **autouse fixture** 每个测试前重置。

我们项目已有这个模式的雏形：`payment_mode` fixture 用 `/debug/payment-mode` 切换 + 测试后恢复。顺着这个模式补 `/debug/reset`。

## 方案（三处改动）

1. `server/main.py`：记录 `_INITIAL_STOCK` 快照 + 加 `/debug/reset` 接口
2. `api/api_client.py`：加 `reset()` 方法
3. `conftest.py`：加 `reset_data` autouse fixture

### autouse（/ˈɔːtoʊ juːz/）

`@pytest.fixture(autouse=True)`：**不用测试函数显式声明，pytest 自动在每个测试前执行**。适合"每个测试都要做"的全局动作（如 reset）。

为什么不用显式参数？18 个测试每个都要 reset，显式写太啰嗦，且新测试容易忘记——autouse 保证"忘不掉"。

### global 关键字

函数内要**修改**模块级变量（`ORDER_SEQ`、`PAYMENT_MODE`）必须先 `global` 声明，否则 Python 会把它当局部变量处理。

## 为什么 reset 接口放 DEBUG_MODE 块

`/debug/reset` 和 `/debug/payment-mode` 一样，都在 `if os.getenv("DEBUG_MODE") == "1":` 里。生产环境不该暴露 reset 接口，否则别人能一键清空数据库。

## 踩坑记录

- 服务器启动必须带 `DEBUG_MODE=1`，否则 `/debug/*` 接口不注册，`payment_mode` 和 `reset_data` fixture 都会 404。

## 用户问过的问题

- **多个 `autouse=True` 的 fixture 按什么顺序执行？** 可以有多个，不限制数量。执行顺序三条规则：① scope 大的先（session → module → class → function）② 相同 scope，被依赖的先执行 ③ 都没依赖关系，按源码定义顺序。`autouse` 本身不改变排序。⚠️ 有先后顺序要求时，应显式声明依赖（如 `def log_test_start(reset_data)`），别靠定义顺序（脆弱，重排就坏）。
