# ShopHub 性能测试 — 问题与解决记录

> 配套 `PERF_TEST_PLAN.md`（计划）与 `PROGRESS.md`（进度）。本文只记「踩过的坑 → 根因 → 解法」，
> 供面试复盘与后续复现参考。按类别归组，不按时间。

## 一、环境 / 工具类

### 1. 300+ 并发大量 ConnectionRefusedError（WinError 10061）
- **现象**：Locust 跑到 300–500 并发档时，客户端大量 `ConnectionRefusedError(10061)`，请求根本连不上。
- **根因**：单 worker uvicorn 在 Windows 的 ProactorEventLoop 下，accept 新建连接有上限（约 ~400）。
  用 Python 多线程实验复现：100 并发 0 失败，400 并发 417 个 ConnectionError。
- **解决**：`uvicorn --workers 4` 多 worker 部署，把 accept 压力分摊到 4 个进程。

### 2. 残留 worker 进程导致端口冲突 / 请求被路由到旧进程
- **现象**：改完代码重启压测时，报 `WinError 10048`（端口占用）、`WinError 10022`（sock.listen 失败，第 4 个 worker 起不来）；请求打到旧进程，读到旧逻辑 / 旧数据。
- **根因**：Windows 下 `taskkill //F //PID` 只杀父进程，不带 `/T` 杀不掉 `multiprocessing.spawn` 出来的 worker 子进程。多次重启后残留多个进程同时监听 8000。
- **解决**：`powershell "Get-Process python | Stop-Process -Force"` 一次性杀掉所有 python 进程，再重启。

### 3. Windows 控制台输出乱码（GBK）
- **现象**：Locust / pytest 控制台中文输出乱码。
- **根因**：Windows 终端默认 GBK 编码，脚本输出 UTF-8 中文。
- **解决**：不影响功能；需要时 `chcp 65001` 或设 `PYTHONIOENCODING=utf-8`。

## 二、压测脚本 / 方法论类

### 4. Locust `User.run()` 方法名冲突
- **现象**：把 task 方法命名为 `run`，Locust 启动报 "User.run() is a method used internally by Locust"。
- **根因**：`run` 是 Locust `User` 内部保留方法。
- **解决**：改名 `do_task`。

### 5. order 场景 100% 401 失败死循环
- **现象**：压测下单场景，登录失败的 user 拿到 `token=None`，后续所有下单请求都 401，错误率爆炸（baseline_order_50 曾出现 8 万+ 次 401）。
- **根因**：`on_start` 登录一次失败就没重试，token 为 None 后 task 里仍用 `Bearer None` 下单，全部 401。
- **解决**：locustfile 加 `_ensure_login_and_cart()` —— 登录失败重试 3 次；task 内加 token 保护，登录仍失败则跳过该轮（不再发必败请求）。

### 6. 档位间污染
- **现象**：前一档并发把服务打崩 / 打挂，后一档接着跑，结果全废。
- **根因**：服务过载后未恢复。
- **解决**：workers 调大消除过载后自然消除；压测档位之间给服务留恢复时间。

## 三、环境变量 / 配置类

### 7. DEBUG_MODE 多 worker 下不生效（/debug/* 返回 405）
- **现象**：开 `--workers 4` 后，压测 / 测试调 `/debug/reset`、`/debug/payment-mode` 返回 405。
- **根因**：`DEBUG_MODE` 是主进程环境变量，`multiprocessing` spawn 子进程不会继承（Windows spawn 语义），
  所以 worker 里 `os.getenv("DEBUG_MODE")` 为空，故障注入路由没注册。
- **解决**：把 `DEBUG_MODE=1` 写进 `.env`，靠应用启动时 `load_dotenv` 让每个 worker 都读到。

### 8. load_dotenv 无参数不可靠
- **现象**：`load_dotenv()` 无参调用在多 worker 下读不到 .env。
- **根因**：`find_dotenv()` 依赖当前工作目录，多 worker / 不同启动方式下 cwd 不同，行为不可靠。
- **解决**：改显式路径 `load_dotenv(Path(__file__).resolve().parent.parent / ".env")`。

## 四、数据库 / 连接池类（核心瓶颈）

### 9. 每请求新建 MySQL 连接是全局硬瓶颈
- **现象**：压测吞吐上不去。
- **诊断数据**：

  | 测量 | 结果 |
  |------|------|
  | 串行建连（connect+close） | 24.8 ms/次 |
  | 复用连接查询 | 0.36 ms/次 |
  | 40 线程并发建连 | 吞吐仅 67 建连/s（P50 514ms） |

- **结论**：建连开销是查询的 ~70 倍，并发下建连吞吐坍缩。
- **解决**：引入 DBUtils PooledDB 连接池（见下）。

### 10. MySQL max_connections=151 上限被撞穿
- **现象**：无连接池时并发建连，MySQL 报 too many connections。
- **解决**：`max_connections` 151 → 800。

### 11. thread_cache_size=9 太小
- **现象**：并发建连时 MySQL 线程缓存耗尽，建连退化（更慢）。
- **解决**：`thread_cache_size` 9 → 128。

### 12. anyio 默认线程池 40 上限阻塞请求
- **现象**：FastAPI 同步端点靠 anyio 线程池跑，默认 40 token，高并发下请求排队甚至拒绝连接。
- **解决**：`main.py` lifespan 里 `to_thread.current_default_thread_limiter().total_tokens` 调大
  （默认 100，可配 `THREADPOOL_SIZE` 环境变量）。

### 13. DBUtils autocommit 必须 False
- **现象**：连接池接上后，`conn.begin()/commit()/rollback()` 报错。
- **根因**：现有 db.py 用显式事务，autocommit=True 会与之冲突。
- **解决**：`PooledDB(..., autocommit=False)`；`reset=True` 让连接归还时自动回滚未提交事务；
  `get_conn()` 返回池连接，`conn.close()` 语义变为「归还池」。init_db 里的无库连接保持原生 `pymysql.connect`。

## 五、故障注入 / 测试类

### 14. 多 worker 下 PAYMENT_MODE 故障注入失效（test_pay_timeout 失败根因）
- **现象**：改完连接池后跑全量接口测试，42 通过 / 1 失败，失败项 `test_pay_timeout_triggers_retry`
  （期望支付超时抛 `requests.Timeout`，实际 `DID NOT RAISE`，整测 0.48s 秒完）。
- **根因**：**不是连接池引入的回归**。`PAYMENT_MODE` 是每个 worker 进程内的全局变量。
  当时 8000 端口跑的是 4-worker 压测服务器（残留进程），`/debug/payment-mode` 只把 "timeout" 设到其中一个 worker；
  随后 `/order/{id}/pay` 被负载均衡到另一个 `PAYMENT_MODE` 仍是 success 的 worker，直接支付成功返回，不 sleep、不超时。
- **佐证**：整测 0.48s 完成 → 说明 pay 请求没走 6s sleep；且当时 8000 上残留了 2 个 uvicorn 父进程 + 4 个 spawn 子进程（`--workers 4`）。
- **解决**：接口测试要跑在**单 worker** 服务器上——故障注入是进程级状态，多 worker 天然不一致。
  清理残留进程后，用 `uvicorn server.main:app`（不带 `--workers`）重启即可。
- **结论**：连接池改动本身无回归（其余 42 项全过，唯一失败项是上述环境问题）。

### 15. 压测数据被功能测试的 reset_data 清空（login 全 401，TPS 虚高 20 倍）
- **现象**：连接池后跑 pooled 梯度，login TPS 异常飙升到 ~1863（baseline 才 95，PBKDF2 单次 39.78ms 单核最多 25 TPS，物理上不可能）。
- **根因**：压测前跑了接口测试，其 autouse fixture `reset_data` 会 `TRUNCATE` 所有表再插种子，把 1000 个 `perfuser` 账号 + 100 万库存全删了。
  login 因账号不存在直接 401（不跑 PBKDF2），响应时间从 40ms 掉到 26ms，TPS 虚高，数据全废。
- **佐证**：`SELECT COUNT(*) FROM users WHERE username LIKE 'perfuser%'` 返回 0，总用户只剩 4 个种子账号。
- **解决**：重跑 `perf/seed_data.py` 铺数据，再验证「账号数 = 1000、login 返回 200、耗时 ~40ms」后才重新压测。
- **教训**：压测数据和功能测试数据要隔离；压测前先验证数据（账号存在 + login 200 + TPS 量级合理），别盲信「跑完了」。

---

## 关键认知（面试 / 复盘用）

1. **性能测试与功能测试要用不同的服务器配置**：压测要 4 worker 摊 accept 压力、要连接池；
   功能测试（尤其故障注入类）要单 worker，保证进程级状态一致。
2. **瓶颈要拿数据说话**：先测「建连 vs 查询」的耗时差（70 倍），再决定优化方向，不拍脑袋上连接池。
3. **Windows 部署注意点**：`--workers` 用 spawn 语义（环境变量不继承）、taskkill 要带 `/T` 或直接按进程名清。
4. **压测结果要过「物理合理性」自检**：PBKDF2 单次 39.78ms 就决定了 login TPS 有天花板，看到 20 倍异常提升先怀疑数据被污染，而不是以为优化生效。
