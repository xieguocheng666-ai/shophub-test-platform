# 压测环境隔离 Checklist

> 来源：ShopHub 压测项目 15 个踩坑复盘（详见 `PROBLEMS.md`）。下次跑压测/性能测试前，照此逐项勾选。
> 核心教训：多数返工不是技术难点，是「环境 / 数据 / 进程」三样没隔离。

## A. 环境隔离（压测 vs 功能测试，动手前定死）

- [ ] **两套启动脚本分开，不混用**：压测用 `--workers 4` 分摊 accept 压力，功能测试用单 worker。
  - 原因：故障注入（`PAYMENT_MODE`）、`DEBUG_MODE` 是「进程级全局变量」，多 worker 下 `/debug/*` 只改到其中一个进程，测试结果必然不一致（曾误判为连接池回归）。
- [ ] **环境变量写进 `.env`**，靠 `load_dotenv` 让每个 worker 读到；不靠主进程环境变量传。
  - 原因：Windows `--workers` 用 spawn 语义，子进程不继承主进程 env（`DEBUG_MODE` 多 worker 失效的根因）。
- [ ] **明确当前端口跑的是哪套**，改代码后先确认重启的是目标进程，别让请求打到旧进程。

## B. 数据隔离（压测前）

- [ ] **压测数据独立**：账号用前缀（如 `perfuser`）、库存铺足（如 100 万）；功能测试的 `reset_data` 不要 TRUNCATE 压测表。
  - 原因：功能测试 autouse fixture 曾把 1000 账号 + 100 万库存清空，login TPS 虚高 20 倍、整档作废。
- [ ] **跑前先验证数据**：`SELECT COUNT(*)` 账号数正确 → login 返回 200 → 单次耗时量级合理（如 PBKDF2 ~40ms）。
- [ ] **备好铺数据脚本**（`seed_data.py`），数据被清后能一键重铺。

## C. 进程清理

- [ ] **写 kill-all 脚本**：Windows 下 `taskkill //F //T //PID` 或按进程名清（`Get-Process python | Stop-Process -Force`）。
  - 原因：`taskkill //F //PID` 不带 `/T` 杀不掉 spawn 出来的 worker 子进程，多次重启后残留进程同时监听 8000 → 端口冲突（10048）、请求打到旧进程。

## D. 配置调优（排除非目标瓶颈）

- [ ] MySQL：`max_connections` 151→800、`thread_cache_size` 9→128（防 too many connections / 建连退化）。
- [ ] anyio 线程池：`to_thread.current_default_thread_limiter().total_tokens` 调大（默认 40，高并发会排队甚至拒绝连接）。

## E. 物理合理性自检（压测中/后）

- [ ] **先算天花板**：CPU 密集操作（PBKDF2 单次 39.78ms）决定单核 TPS 上限（~25），看到 20 倍异常提升先怀疑数据被污染，而非以为优化生效。
- [ ] **全程 0 错误才采信**；出现 401 先查登录链路（`on_start` 登录失败 → `token=None` → 全 401 死循环）。
- [ ] **档位间留恢复时间**，前一档把服务打崩后，后一档结果作废。

## 一句话总结

先隔离环境 → 再隔离数据 → 备好进程清理 → 结果过物理自检。这四步做完，压测的坑能砍掉一大半。
