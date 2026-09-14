# 04 · 并发防超卖

> 阶段0 · 概念 4 ｜ 讲解 + 提问沉淀 ｜ 2026-08-25
> 核心术语：并发 concurrency /kənˈkʌrənsi/、竞态条件 race condition /reɪs kənˈdɪʃn/、临界区 critical section /ˈkrɪtɪkl ˈsekʃn/、互斥锁 mutex /ˈmjuːtɛks/、丢失更新 lost update /lɒst ʌpˈdeɪt/、线程池 thread pool /θred puːl/、死锁 deadlock /ˈdedlɒk/

## 1. 为什么需要防超卖

库存 `stock` 是**很多请求同时读写的一份共享数据**。并发下多个用户同时下单最后几件，如果不加保护，会卖出超过库存的货——这叫**超卖**（oversell /ˌoʊvərˈsel/）。

```
                    多个下单请求（并发）
                          │
         ┌────────────────┼────────────────┐
         ▼                ▼                ▼
     线程1 下单        线程2 下单       线程3 下单
         └────────────────┼────────────────┘
                          ▼
                   【库存 stock】  ← 唯一的共享资源
```

## 2. 超卖是怎么发生的：竞态条件

"校验库存"和"扣库存"是**两步**，中间有空隙。两个用户同时下单最后 1 件（库存剩 1）：

```
时间轴          线程A                    线程B
 t1           读 stock → 1
 t2                                      读 stock → 1   （也读到 1）
 t3           判断 1 >= 1，通过 ✓
 t4                                      判断 1 >= 1，通过 ✓
 t5           扣减 → stock = 0
 t6                                      扣减 → stock = -1  ❌ 超卖！
```

**根因**：读和写之间被另一个人插队，读到了**过期**的库存。这就是**竞态条件**（race condition）——结果取决于谁先谁后，而这个顺序不可控。

## 3. 丢失更新：回补不加锁的危害

`+=` 和 `-=` 看着是一步，底层是**读 → 算 → 写**三步。若 `_restore_stock`（回补）不加锁，和下单扣减并发会**丢失更新**：

```
库存 stock 初始 = 10，正确结果（串行）10 - 5 + 3 = 8

不加锁，三步交叉：
  ① A（扣5）读 stock = 10
  ②                    B（回补3）读 stock = 10   ← A 还没写回，读到旧值
  ③ A 算 10-5 = 5
  ④                    B 算 10+3 = 13
  ⑤ A 写 stock = 5
  ⑥                    B 写 stock = 13   ← 覆盖了 A 的 5！

最终 stock = 13，但正确是 8。A 的扣减被 B 覆盖丢了 → 库存凭空多 5 件。
```

## 4. 解决方案：锁把「校验+扣减」捏成临界区

**临界区**（critical section）：一段"同一时刻只放一个人进去"的代码。实现工具是**互斥锁**（mutex）。

> **锁锁的不是数据变量，而是"那段读写数据的代码"。** 约定：所有改 stock 的代码都统一走同一把锁。谁拿到锁谁独占临界区，别人排队。

```
   线程A ──→ 拿锁 ──→ [校验+扣减] ──→ 释放锁
                                       │
   线程B ──→ 排队等锁 ──→（等A做完）──→ 拿锁 ──→ [校验+扣减] ──→ 释放锁
```

锁不是让并发变快，而是让"读+改"这段必须独占。

## 5. 代码实现

### 5.1 造锁（server/main.py:30）

```python
STOCK_LOCK = threading.Lock()
```

| 方法 | 含义 |
|------|------|
| `acquire()` /əˈkwaɪər/ | 拿锁：门开就进去并锁门；门关就排队等 |
| `release()` /rɪˈliːs/ | 释放锁：开门放下一个进来 |

### 5.2 下单扣库存（server/main.py:226-235）

```python
with STOCK_LOCK:                       # with 自动 acquire，块结束自动 release
    for pid, qty in cart.items():      # 第一遍：校验所有商品够不够
        product = next(p for p in PRODUCTS if p["id"] == pid)
        if product["stock"] < qty:
            raise HTTPException(400, ...)
    for pid, qty in cart.items():      # 第二遍：全部够了才统一扣
        next(p for p in PRODUCTS if p["id"] == pid)["stock"] -= qty
```

- **先验后扣分两遍**：避免"扣到一半才发现不够"。
- **用 `with` 不用手动 acquire/release**：校验失败会 `raise` 跳出，`with` 保证即使抛异常也释放锁。手动写一旦异常忘了 `release`，锁永远关着 → **死锁**（deadlock）。

### 5.3 回补也加锁（server/main.py:123-128）

```python
def _restore_stock(order):
    with STOCK_LOCK:
        for item in order["items"]:
            product = next(...)
            product["stock"] += item["quantity"]   # 读-改-写，也要独占
```

只要改 stock 的地方，都用**同一把**锁——回补和下单访问同一个共享数据，必须进同一道门。

## 6. 并发测试怎么写（api/test_api.py）

```python
def test_concurrent_order_no_oversell(base_url, config):
    token = 登录 bob 拿到的 token
    product_id = 3
    stock = 商品3的库存              # 200

    def place_order(_):              # 每个线程干的事
        加购 1 件
        return 下单状态码            # 200 成功 / 400 失败

    with ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(place_order, range(stock + 30)))  # 230 次

    success = sum(1 for c in results if c == 200)
    final_stock = 商品3最终库存
    assert success <= stock          # 不超卖
    assert final_stock >= 0          # 库存不为负
```

三个关键 API：

| 代码 | 作用 |
|------|------|
| `ThreadPoolExecutor(max_workers=20)` | 线程池：雇 20 个工人（线程），任务派给空闲的，循环利用 |
| `ex.map(place_order, range(230))` | 派 230 个任务给工人并发跑；`range` 的数字只是凑任务数 |
| `list(...)` | `map` 是惰性的，`list()` 逼它真跑完并收成 230 个状态码列表 |

`place_order(_)` 的 `_`：接住 range 塞进来的数字（0,1,2…），但用不到，占位。

`sum(1 for c in results if c == 200)`：计数惯用法，数有几个 200。等价于 for 循环里 `success += 1`。

## 7. 断言设计：为什么 `<=` 而不是 `==`

并发下结果**不唯一**（这次 198 成功、下次 200），但安全底线唯一。

- 锁保证两件事（**必然成立**）：`success <= stock`（不超卖）、`final_stock >= 0`（库存不为负）。
- "恰好卖光 200 件"是**碰运气**的结果——线程互相清空共享购物车，可能少卖几件，这**不是 bug**。

```
断言应该测"必然成立的底线"，而不是"碰巧出现的结果"。
== 卡"确定的结果"，<= / >= 卡"不能越过的边界"。
```

## 8. 你问过的问题速查

| 你的问题 | 一句话答案 |
|---------|-----------|
| ThreadPoolExecutor / map / list 是干嘛的 | 雇工人（线程池）→ 派任务（map 并发）→ 收结果（list 逼它跑完） |
| `sum(1 for ...)` 里的 1 是什么 | 计数惯用法：每个命中项吐一个 1，sum 累加 = 有几个满足条件 |
| 为什么不断言 `success == stock` | 并发下"恰好卖光"不保证发生，会误报失败；只断言不超卖、不为负这两个必然成立的底线 |
| 删掉 `_restore_stock` 的锁会怎样 | 丢失更新：回补和扣减的"读改写"三步交叉，一个覆盖另一个，库存被算错 |
| 锁锁的是什么 | 锁的是"读写 stock 的那段代码"，不是 stock 变量本身；约定所有改 stock 的代码走同一把锁 |

## 9. 动手任务（已完成 ✅）

- `test_pay_fail_restores_all_products_stock`（补概念 3）：fail 下两个商品回补到下单前
- `test_concurrent_order_no_oversell_another`：商品 4（stock=30）并发 `stock+20` 次，不超卖不为负

两个用例 + 现有 `test_concurrent_order_no_oversell` 共 3 个并发/回补测试，全绿。
