# 03 · 模拟支付网关（故障注入）

> 阶段0 · 概念 3 ｜ 讲解 + 提问沉淀 ｜ 2026-08-24
> 核心术语：故障注入 fault injection（fault /fɔːlt/，injection /ɪnˈdʒekʃn/）、网关 gateway /ˈɡeɪtweɪ/、超时 timeout /ˈtaɪmaʊt/

## 1. 为什么需要模拟支付网关

真实世界里，支付不是本地代码直接算的——系统要调第三方支付宝/微信接口。而测试要覆盖「支付失败」「支付超时」这些异常场景时，**真实的支付宝不会配合你演「余额不足」**。

所以要在 SUT 里放一个**假的支付网关**，并能**人为控制它的行为**。这个「人为控制」就叫故障注入。

```
真实世界：                        我们的 SUT（测试靶子）：
用户点支付                        用户点支付
   │                                │
   ▼                                ▼
调支付宝/微信接口 ──→ 返回结果      调「假的网关」──→ 返回结果
   │                                │
   │ 余额不足？网络卡了？            │ 测试时人为让它：
   │ 我们【控制不了】                │   成功 / 余额不足 / 卡死
```

## 2. 核心概念：故障注入

**故障注入**：在系统里埋一个「开关」，测试时人为把某个环节**弄坏**，观察被测代码和测试框架怎么应对。

一句话：**平时正常，测试时一键变坏。**

在这个项目里，那个开关就是全局变量 `PAYMENT_MODE`，能切 3 个档位：

| 模式 | 模拟的现实 | 服务端干的事 | 返回的 HTTP | 订单结局 |
|------|-----------|-------------|------------|---------|
| `success` | 支付正常 | 直接置 `paid` | 200 | → PAID |
| `fail` | 余额不足（业务拒绝） | 回补库存 + 置 `canceled` | 200（但业务失败） | → CANCELED |
| `timeout` | 网关卡死（系统故障） | `sleep 6s` 不响应 | 客户端超时 | 保持 PENDING |

## 3. 最关键的一层：业务失败 ≠ 系统故障

面试官最爱追问的点。`fail` 和 `timeout` 虽然都是「没支付成功」，本质完全不同：

| | `fail`（业务失败） | `timeout`（系统故障） |
|---|---|---|
| 发生了什么 | 请求**成功到达**，网关**明确拒绝**（余额不足） | 请求**没完成**，网关**卡住不响应** |
| 重试有用吗 | ❌ 重试一万次余额也不会变多 | ✅ 卡死是临时的，重试可能就通 |
| 测试框架怎么做 | **不重试**，订单直接终态（CANCELED） | **重试**（`ApiClient._request` 里的 retry） |
| HTTP 怎么表达 | `200` + body 业务状态 `failed` | 直接不响应，客户端自己超时 |

**为什么分这么细**：重试有副作用（重复扣款）。`fail` 返回 `200`，**不走 `_request` 的异常分支，所以不触发重试**；`timeout` 抛 `requests.Timeout`（属 `RequestException`），才触发重试。

> 面试题：`fail` 为什么用 `200` 而不是 `500`？—— 因为 500 是「服务器出错了」，而余额不足是「请求正常处理了、业务上拒绝」，属于业务失败不是系统故障。

## 4. 代码实现

### 4.1 开关定义（server/main.py:25-27）

```python
PAYMENT_MODE = "success"          # 故障注入开关，默认正常
PAYMENT_TIMEOUT_SLEEP = 6         # timeout 挂起秒数，必须 > 客户端超时
```

### 4.2 开关怎么被改（server/main.py:304-316）

```python
if os.getenv("DEBUG_MODE") == "1":          # 仅 DEBUG 才注册这个后门路由

    class PaymentModeRequest(BaseModel):
        mode: str

    @app.post("/debug/payment-mode")
    def set_payment_mode(req: PaymentModeRequest):
        global PAYMENT_MODE                   # 改模块级变量必须声明 global
        if req.mode not in ("success", "fail", "timeout"):
            raise HTTPException(400, "mode 必须是 success/fail/timeout")
        PAYMENT_MODE = req.mode
        return {"payment_mode": PAYMENT_MODE}
```

- `global`：函数里要**修改**模块级变量，必须声明，否则 Python 以为它是局部变量，改不到外面那个真开关。
- `DEBUG_MODE` 保护：故障注入是「测试专用后门」，生产环境若暴露，任何人都能 POST 一下把支付切到 `fail` 搞垮系统。

### 4.3 pay_order 逐行（server/main.py:270-286）

```python
@app.post("/order/{order_id}/pay")
def pay_order(order_id: int, user: str = Depends(_require_user)):
    order = _find_order(user, order_id)          # 校验存在 + 归属（越权 403）
    if order["status"] != PENDING:               # 状态机守卫：只有待支付能付
        raise HTTPException(400, "订单状态不允许支付")

    if PAYMENT_MODE == "timeout":
        time.sleep(PAYMENT_TIMEOUT_SLEEP)        # sleep 后【没有 return】，继续往下掉

    if PAYMENT_MODE == "fail":
        _restore_stock(order)                    # 先回补库存
        _transition(order, CANCELED)             # 再置 canceled
        return {"status": "failed", "reason": "余额不足", "order_id": order_id}

    _transition(order, PAID)                     # 兜底（success）：PENDING → PAID
    return {"status": "paid", "order_id": order_id}
```

⚠️ 细节：`timeout` 分支 sleep 完**不会返回**，会落到最后的 `_transition(order, PAID)`。即：客户端 `timeout=1` 等 1 秒放弃，但服务端 sleep 6 秒后**仍会把订单置成 `paid`**（只是响应没人接收）。所以 timeout 的本质是「响应慢到客户端等不及」，测试断言的是**客户端超时 + 重试被触发**，不是「服务端挂了」。

### 4.4 payment_mode fixture（conftest.py:42-52）

```python
@pytest.fixture()
def payment_mode(api_client):
    def _set(mode):                        # 内部函数：封装「切模式」动作
        resp = api_client.set_payment_mode(mode)
        assert resp.status_code == 200, resp.text
        return resp

    _set("success")   # ① setup：进测试前先归位
    yield _set        # ② 把 _set 函数交出去（不是调用结果！）
    _set("success")   # ③ teardown：测试后还原
```

- **`yield _set` 交出去的是函数本身**（不带括号），不是 `_set("success")` 的调用结果。
- 所以测试里 `payment_mode` 这个参数拿到的就是 `_set` 函数，才能 `payment_mode("fail")` 调用、传参。
- 为什么交函数而不是固定值：同一个测试可能要切多次模式（先 fail 后 success），需要「随时能调用的切换工具」。

## 5. 三个测试用例（test_api.py:99-135）

| 用例 | 验证 |
|------|------|
| `test_pay_fail_cancels_and_restores_stock` | fail → 200 但业务 `failed` + 订单 canceled + 库存回补 |
| `test_pay_fail_then_repay_rejected` | fail 取消后切回 success 再付 → 400（状态机守卫） |
| `test_pay_timeout_triggers_retry` | timeout + 短超时 client → `pytest.raises(requests.Timeout)` |

## 6. 你问过的问题速查

| 你的问题 | 一句话答案 |
|---------|-----------|
| 为什么测试函数能直接 `payment_mode("fail")`？没见过往夹具传参 | fixture 返回的是 `yield` 出来的 `_set` **函数**，不是 fixture 本身；`payment_mode("fail")` 是**调用函数传参**，不是「给 fixture 传参」 |
| `with STOCK_LOCK` 防超卖是怎么回事？ | 并发下两个请求都读到旧库存导致超卖；锁让「校验+扣减」变成原子操作，同一时刻只放一个请求进去 —— 详见概念 4（并发防超卖） |

## 7. 动手任务（待做）

写 `test_pay_fail_restores_all_products_stock`：fail 模式下加两个不同商品，支付失败后**两个商品**库存都回补到下单前。

验收：`pytest api/test_api.py::test_pay_fail_restores_all_products_stock -v` 跑通。
