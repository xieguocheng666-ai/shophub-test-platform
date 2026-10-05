# ShopHub 性能测试 — 进度追踪（临时文档）

> 计划见 `PERF_TEST_PLAN.md`。本文件只记进度与关键数据，最终报告另写 `report.md`。

## 一、环境（含压测环境调优）

| 项 | 值 |
|----|----|
| Python | 3.11.9（项目 .venv） |
| Locust | 2.46.6 |
| DBUtils | 已装 |
| MySQL | 8.0，127.0.0.1:3306，库 shop |
| SUT | FastAPI，127.0.0.1:8000 |

**压测环境调优（为排除非目标瓶颈，聚焦「每请求建连」）：**

| 参数 | 默认 | 压测值 | 原因 |
|------|------|--------|------|
| uvicorn workers | 1 | **4** | 单 worker 在 Windows 上 ~400 并发拒绝连接（ConnectionRefusedError） |
| anyio 线程池 | 40 | **200/进程** | `main.py` lifespan 里 `total_tokens` 调大 |
| MySQL max_connections | 151 | **800** | 无连接池时并发建连会撞 151 上限 |
| MySQL thread_cache_size | 9 | **128** | 并发建连时线程缓存太小导致建连退化 |

## 二、已完成

1. ✅ `perf/locustfile.py`：三接口 task，`SCENARIO` 切换，`ORDER_MODE`（hot/spread）。
2. ✅ `perf/seed_data.py`：1000 压测账号 + 库存铺 100 万。
3. ✅ 三接口 + 下单闭环验证通过。
4. ✅ **瓶颈诊断**（核心证据）：

| 测量 | 结果 |
|------|------|
| 串行建连（connect+close） | **24.8 ms/次** |
| 复用连接查询 | **0.36 ms/次** |
| 40 线程并发建连 | 吞吐仅 **67 建连/s**（P50 514ms） |

> 结论：建连开销是查询的 ~70 倍，并发下建连吞吐坍缩。「每请求新建 MySQL 连接」是全局硬瓶颈。

5. ✅ **方法论踩坑与修复**（重要）：

| 问题 | 根因 | 修复 |
|------|------|------|
| 300+ 并发大量 ConnectionRefusedError | 单 worker Windows ProactorEventLoop accept 上限 | `--workers 4` |
| order 场景 100% 401 失败 | on_start 登录失败→token=None→下单 401 死循环 | locustfile 加 `_ensure_login_and_cart`（重试 3 次）+ task 内 token 保护 |
| 档位间污染 | 前一档打崩服务，后一档接着跑全废 | workers 调大后消除过载 |

## 三、最终结果（已全部完成）

- ✅ 基线压测（无连接池）：3 接口 × 5 档，`perf/results/baseline_*.csv`
- ✅ 引入 DBUtils 连接池优化 `server/db.py`
- ✅ 连接池后重跑同梯度，`perf/results/pooled_*.csv`
- ✅ 行锁竞争专项（`ORDER_MODE=hot`），`perf/results/hot_order_*.csv`
- ✅ 汇总数据 + 写 `report.md`（含拐点曲线 `results/charts/*.png`）
- ✅ 回填简历 HTML + 导出 PDF（`D:\简历\成品PDF\谢国城-软件测试-简历.pdf`）

**核心数据（全程 0 错误）：**

| 接口 | 优化前 TPS 峰值 | 优化后 TPS 峰值 | 提升 |
|------|----------------|----------------|------|
| 登录 | 96.6 | 188.1 | 1.8 倍 |
| 搜索 | 183.9 | 1761.0 | 9.6 倍 |
| 下单 | 29.4 | 496.0 | 16.9 倍 |

行锁竞争：hot（抢同商品）vs spread（分散）吞吐下降 **14%–24%**。

## 五、关键决策记录

- **连接池改造方案**：`PooledDB(creator=pymysql, maxconnections=50, mincached=5, maxcached=50, blocking=True, ping=1, reset=True, autocommit=False)`；`get_conn()` 返回 `_get_pool().connection()`，`conn.close()` 语义变为「归还池」；`init_db()` 里「无库连接」保持原生 `pymysql.connect`。
- **autocommit 必须 False**：现有代码用 `conn.begin()/commit()/rollback()`，autocommit=True 会报错；`reset=True` 归还时自动回滚未提交事务。
- **多 worker + 连接池**：4 worker 各进程独立 PooledDB，每进程 max 50 连接，总 200 < 800，符合生产多进程部署。
