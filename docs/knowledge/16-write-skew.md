# 16 · 并发确认收货与写偏斜

> 阶段2 · 并发 bug 修复沉淀 ｜ 2026-09-02 ｜ 教学待安排（先沉淀后讲解）
> 核心术语：写偏斜 write skew /raɪt skjuː/、隔离级别 isolation level /ˌaɪsəˈleɪʃn ˈlevl/、可重复读 REPEATABLE READ /rɪˈpiːtəbl riːd/、快照读 snapshot read /ˈsnæpʃɒt riːd/、当前读 current read /ˈkʌrənt riːd/、悲观锁 pessimistic lock /ˌpesɪˈmɪstɪk lɒk/、死锁 deadlock /ˈdedlɒk/

## 1. 现象：父订单 stuck 在 paid

前端全链路验证时，买家对跨店铺订单（拆成 2 个子订单）**并发**点击「确认收货」，结果两个子订单都 `completed` 了，父订单却卡在 `paid` 不转 `completed`。顺序操作正常，只有并发才触发。

## 2. 根因：快照读 + REPEATABLE READ

`confirm_sub_order` 判断「父订单该不该转 completed」靠一句统计：

```sql
SELECT COUNT(*) FROM sub_orders
WHERE parent_order_id=%s AND status != 'completed'
```

如果 `COUNT == 0`，说明所有子订单都完成了，才把父订单改成 `completed`。

问题出在这句 `SELECT` 是**快照读**（snapshot read）。MySQL 默认隔离级别 **REPEATABLE READ**（可重复读）下，普通 `SELECT` 读的是**事务开启那一刻的快照**，不是最新数据。

两个线程并发确认时，各自事务的快照里，都还「看得到」对方那个尚未完成的子订单：

```
时间轴           事务 A（确认子单1）        事务 B（确认子单2）
 t1             UPDATE 子单1 → completed
 t2                                      UPDATE 子单2 → completed
 t3             COUNT(未完成子单) → 1       ← 快照里还看到子单2是 shipped
 t4                                      COUNT(未完成子单) → 1  ← 快照里还看到子单1是 shipped
 t5             因为 COUNT=1，不更新父单
 t6                                      因为 COUNT=1，不更新父单
 t7             commit                    commit

结果：两个子单都 completed，父单却停在 paid —— 谁都没去改它。
```

## 3. 这个 bug 的正式名字：写偏斜（write skew）

两个事务**读同一份旧数据**，基于这份旧数据**各自做不同的写**，最终破坏了一致性约束（「所有子订单完成 ⇒ 父订单完成」）。

它和「丢失更新 / 超卖」不同：超卖是两个事务改**同一行**；写偏斜是两个事务改**不同的行**（各改各的子订单），却靠读到的旧快照共同决定是否改**第三处**（父订单）。所以光给子订单行加锁挡不住它——必须锁住那个「被共同依赖的读」的源头。

## 4. 修复：`SELECT ... FOR UPDATE` 悲观锁

核心是**快照读 → 当前读**的切换：

| 读法 | 语义 | 并发下 |
|------|------|--------|
| `SELECT` | 快照读，读事务开启时的旧版本 | 可能读到过期数据（本 bug 根源） |
| `SELECT ... FOR UPDATE` | 当前读，读最新已提交版本，并给读到的行加 X 锁 | 读到最新 + 锁住行，别人改不了 |

修复后的 `confirm_sub_order`（server/db.py）加了两把锁：

```python
conn.begin()
with conn.cursor() as cur:
    # 1. 子订单 shipped → completed（UPDATE 本身就是当前读，拿子订单行锁）
    cur.execute(
        "UPDATE sub_orders SET status='completed' WHERE sub_order_id=%s AND status='shipped'",
        (sub_order_id,),
    )
    if cur.rowcount == 0:
        raise HTTPException(status_code=400, detail="子订单状态不允许确认收货")

    # 2. 锁子订单行，取出 parent_order_id
    cur.execute(
        "SELECT parent_order_id FROM sub_orders WHERE sub_order_id=%s FOR UPDATE",
        (sub_order_id,),
    )
    parent_id = cur.fetchone()["parent_order_id"]

    # 3. 锁父订单行 —— 关键：把「检查+更新父订单」变成临界区
    cur.execute(
        "SELECT order_id FROM orders WHERE order_id=%s FOR UPDATE", (parent_id,)
    )

    # 4. 此时再 COUNT：拿到父订单锁后，看到的必是最新状态
    cur.execute(
        "SELECT COUNT(*) AS n FROM sub_orders WHERE parent_order_id=%s AND status != 'completed'",
        (parent_id,),
    )
    if cur.fetchone()["n"] == 0:
        cur.execute("UPDATE orders SET status='completed' WHERE order_id=%s", (parent_id,))
conn.commit()
```

**为什么锁父订单行就够了**：两个并发事务确认同一父订单下的不同子订单，最终都要锁**同一行** `orders` 记录。谁先拿到父订单行锁，谁先做「COUNT + 更新」；另一个在锁上排队，等前一个提交后，自己再 COUNT 时看到的就是最新状态（前面那个子订单已完成）。

```
事务A：锁子单1 → 锁父订单 ✓ → COUNT=1（子单2没完成）→ 不改父单 → commit 放锁
                                                          │
事务B：锁子单2 → 排队等父订单锁 ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄→ 拿到父订单锁
                                                          → COUNT=0 → 父单 completed ✓
```

## 5. 锁序统一：为什么「子订单 → 父订单」

多个事务如果拿锁的**顺序不一致**，可能互相卡死——这就是**死锁**（deadlock）：

```
事务A：先锁父订单 → 再想锁子单1
事务B：先锁子单1 → 再想锁父订单
→ A 等 B 手里的子单1，B 等 A 手里的父订单，谁都动不了
```

约定**所有**走这条流程的代码都按「先子订单、后父订单」的顺序拿锁，就杜绝了死锁。`confirm_sub_order` 里步骤 2（锁子订单）在步骤 3（锁父订单）之前，就是这个约定。

## 6. 回归测试（api/test_api.py）

```python
@allure.feature("并发确认收货")
def test_concurrent_confirm_parent_completed(base_url, config):
    # alice 跨店铺下单拆 2 子单 → 两店分别发货
    # ThreadPoolExecutor 并发确认两个子订单
    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(confirm, [sub1, sub2]))
    # 断言：两个 confirm 都 200，且父订单最终 completed
    assert all(code == 200 for code in results)
    assert 父订单 status == "completed"
```

单测 1.35s，全量 37 passed。

## 7. 面试追问预判

| 追问 | 一句话应对 |
|------|-----------|
| 什么是写偏斜 | 两个事务读同一份旧快照、各自改不同行，共同破坏了「所有子单完成⇒父单完成」的一致性约束 |
| 快照读和当前读区别 | 快照读读事务开启时的旧版本（REPEATABLE READ 的 MVCC 机制）；当前读读最新已提交版本并加锁。`FOR UPDATE` 把前者变后者 |
| 为什么不用乐观锁/版本号 | 乐观锁靠「提交时发现版本变了就重试」，跨行统计的写偏斜用版本号很难兜住；这里直接锁父订单行最直观，代价是串行化同一父订单的确认 |
| 会不会死锁 | 锁序统一「子订单→父订单」防死锁；只有同一父订单的并发确认会互相排队，粒度足够细 |
| 和防超卖（原子 UPDATE）什么关系 | 防超卖改同一行，原子 UPDATE 一行搞定；写偏斜跨行、且是「读决定写」，必须锁住被共同依赖的那行（父订单） |

## 8. 待沉淀的知识点（编号已预留，未写）

- `12-atomic-oversell.md`：内存 `threading.Lock` → MySQL 原子 `UPDATE ... WHERE stock >= qty` + rowcount
- `13-rbac.md` / `14-password-hash.md` / `15-config.md`：角色越权 / 密码加盐 PBKDF2 / 配置外置

> 本文档只沉淀「写偏斜」这一件事，与上述编号不冲突，避免任何两篇文档重复管同一件事。
