# my-test-framework

一个**自写被测系统 + 接口/UI 双测**的电商测试框架，覆盖「登录 → 搜索 → 购物车 → 下单 → 支付 → 发货 → 确认收货」完整业务闭环。

> 秋招测开方向的核心项目：被测系统（SUT）与测试代码都自己写，从系统架构到测试用例都能讲透，面试可深挖到第三四层。

## 核心亮点

- **自写被测系统**：FastAPI + MySQL 的电商后端，数据可控，接口与 UI 都能测
- **平台模式建模**：一个商家开多店，商品价格/库存挂店铺（`store_product`），订单按店铺拆成「父订单 + 子订单」
- **并发防超卖**：下单时 `UPDATE ... WHERE stock >= qty` 原子扣库存，20 线程并发下单无超卖
- **安全防护**：JWT 鉴权 + RBAC（buyer/seller）+ 水平/垂直越权矩阵全覆盖
- **支付故障注入**：模拟第三方网关 success / fail / timeout 三种模式，验证重试与库存回补
- **异步关单**：Celery + Redis 周期扫描超时未支付订单自动取消（幂等、回补库存）
- **容器化 + CI**：Docker Compose 三层架构（db + 被测系统 + 测试运行器），GitHub Actions 全量跑测试

## 架构

```
┌─────────────────────────── 被测系统 (SUT) ───────────────────────────┐
│  FastAPI 电商后端 (server/)                                          │
│  登录(JWT) → 搜索 → 购物车 → 下单(拆单+扣库存) → 支付 → 发货/收货      │
│            │                                                        │
│            ▼                                                        │
│  MySQL 8.0 (PyMySQL 原生 SQL, server/db.py)                          │
└──────────────────────────────────────────────────────────────────────┘
      ▲ 接口测试 (requests)          ▲ UI 测试 (Playwright)
      │                              │
┌─────┴──────────┐          ┌────────┴─────────────┐
│ api/           │          │ ui/                  │
│  api_client.py │          │  pages/ (POM)        │
│  test_api.py   │          │  test_ui.py          │
└────────────────┘          └──────────────────────┘
      │                              │
      └──────────┬───────────────────┘
                 ▼
        pytest + Allure 报告 / GitHub Actions CI

异步关单：Celery Worker ← Redis (broker) ← 周期任务扫描超时未支付订单
```

## 技术栈

| 层 | 技术 |
|----|------|
| 被测系统 | Python 3.11 · FastAPI · uvicorn · MySQL 8.0 · PyMySQL |
| 鉴权 | JWT (PyJWT) · PBKDF2 密码哈希 · RBAC |
| 接口测试 | pytest · requests · PyYAML 数据驱动 |
| UI 测试 | Playwright · pytest-playwright · POM 模式 |
| 异步任务 | Celery · Redis |
| 报告 | Allure |
| 容器化 | Docker · Docker Compose |
| CI | GitHub Actions |

## 目录结构

```
my-test-framework/
├── server/                 # 被测系统（FastAPI 电商后端）
│   ├── main.py             # 接口层：认证/商品/购物车/订单/支付/故障注入
│   ├── db.py               # 数据访问层：原生 SQL + 原子扣库存 + 订单状态机
│   ├── celery_app.py       # Celery 应用（Redis broker + beat 周期任务）
│   └── tasks.py            # Celery 任务（超时关单薄封装）
├── api/
│   ├── api_client.py       # requests + Session + 重试封装
│   └── test_api.py         # 接口测试（鉴权/状态机/支付异常/并发/越权/拆单/异步）
├── ui/
│   ├── pages/              # POM 页面对象（base_page + 登录/首页/购物车/订单/卖家）
│   └── test_ui.py          # UI 测试（登录跳转/搜索/加购下单/卖家开店）
├── config/config.yaml      # base_url + 测试账号
├── data/                   # 参数化测试数据（yaml）
├── Dockerfile              # 被测系统镜像
├── Dockerfile.test         # 测试运行器镜像（含 Playwright Chromium）
├── docker-compose.yml      # db + sut + worker + test + redis
├── .github/workflows/ci.yml
├── conftest.py             # 全局 fixture（base_url/api_client/reset_data 等）
├── pytest.ini
└── requirements.txt
```

## 快速开始

### 方式一：本地运行

```bash
# 1. 安装依赖
python -m venv .venv && source .venv/Scripts/activate   # Windows
pip install -r requirements.txt
python -m playwright install chromium

# 2. 配置 .env（数据库连接，参考 .env.example）
cp .env.example .env

# 3. 启动被测系统（必须带 DEBUG_MODE=1，否则 /debug/* 故障注入路由不注册）
DEBUG_MODE=1 .venv/Scripts/python -m uvicorn server.main:app --port 8000

# 4. 跑测试
.venv/Scripts/python -m pytest

# 5. Allure 报告
.venv/Scripts/python -m pytest --alluredir=reports
allure serve reports
```

### 方式二：Docker 一键跑测试

```bash
docker compose up --build --abort-on-container-exit --exit-code-from test
```

`docker-compose.yml` 拉起 5 个服务：`db`（MySQL）+ `sut`（被测系统）+ `worker`（Celery+beat）+ `test`（测试运行器）+ `redis`，测试跑完自动退出，退出码即测试结果。

## 测试覆盖

**接口测试（api/test_api.py，38 例）**

| 组 | 场景 | 说明 |
|----|------|------|
| 基础 | 健康检查 / 登录 / 搜索 | 数据驱动 |
| 鉴权 | 缺失/伪造/错误签名/过期/合法 token | JWT 五类边界 |
| 状态机 | 全流程 / 非法流转 / 水平越权 / 垂直越权 | 订单生命周期 |
| 支付异常 | 失败回补库存 / 重付被拒 / 超时重试 / 多商品回补 | 网关故障注入 |
| 并发 | 防超卖 ×2 / 并发确认收货父订单流转 | ThreadPoolExecutor |
| 购物车 | 增删查 / 库存不足 / 数量非法 / 商品不存在 / 空车下单 | 边界场景 |
| 越权矩阵 | 买家开店/上架 / 卖家改他人库存/发货 | RBAC 全覆盖 |
| 拆单 | 跨店拆子订单 / 同商品多店库存隔离 / 跨店失败回补 | 平台模式 |
| 异步任务 | 超时未支付自动关单 | Celery 核心逻辑 |

**UI 测试（ui/test_ui.py，5 例）**：买家登录跳转 / 搜索 / 加购下单全链路 / 卖家登录跳转 / 卖家开店。

## 核心业务设计

- **拆单**：下单时按店铺把购物车商品拆成多个子订单，父订单记录总额，子订单各属一家店
- **防超卖**：扣库存用 `UPDATE store_product SET stock = stock - ? WHERE ... AND stock >= ?`，`rowcount == 0` 即库存不足，天然防超卖
- **越权防护**：订单查询 `WHERE order_id = ? AND username = ?`，店铺操作校验 owner，角色从数据库实时查（不信任客户端 token）
- **支付故障注入**：全局 `PAYMENT_MODE` 切换 success/fail/timeout，模拟网关异常，验证重试与库存回补
- **异步关单**：Celery Beat 周期扫描 `status='pending' AND created_at <= NOW() - INTERVAL ? SECOND` 的订单，幂等取消（条件更新抢占，避免重复回补库存）

更详细的设计见 `CLAUDE.md` 与 `docs/` 目录。
