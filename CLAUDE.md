# my-test-framework（ShopHub 商城测试平台）

> 给 Claude 的快速上手文档：读完这份即可了解项目全貌、怎么跑、关键设计，不必逐文件探索。
> 秋招测开核心项目。**核心产物是测试框架**，被测系统是自写的 FastAPI 电商后端。

## 一、项目是什么

自写一套 FastAPI 电商后端（SUT，`server/`），再用「接口 + UI + 性能」三层测试框架测它，Docker 容器化 + GitHub Actions CI。

- **被测系统 SUT**：模拟真实电商后端，完整业务闭环（登录 JWT → 搜索 → 加购 → 下单 → 支付 → 发货 → 确认收货），数据存 MySQL。
- **接口测试 `api/`**：pytest + requests，yaml 数据驱动。
- **UI 测试 `ui/`**：Playwright + POM。
- **性能测试 `perf/`**：Locust。
- 面试定位：每个模块能追到第三四层（为什么这么做 / 边界怎么处理）。

## 二、技术栈（以代码为准）

| 层 | 技术 |
|----|------|
| 被测系统 SUT | FastAPI + uvicorn + MySQL 8.0（PyMySQL + DBUtils 连接池）+ JWT(PyJWT) + Celery + Redis |
| 接口测试 | pytest + requests + PyYAML 数据驱动 + allure-pytest |
| UI 测试 | Playwright + pytest-playwright + POM |
| 性能测试 | Locust |
| 配置 | PyYAML + python-dotenv（`.env` 存敏感信息） |
| 工程化 | Docker + docker-compose + GitHub Actions |

## 三、目录结构

```text
my-test-framework/
├── server/                  # 被测系统 SUT：FastAPI 电商后端
│   ├── main.py              # 全部路由：认证/商品/购物车/订单/支付/发货/确认/店铺/故障注入
│   ├── db.py                # 数据访问层：连接池 + 所有 SQL + 种子数据 + 原子扣库存 + 幂等关单
│   ├── celery_app.py        # Celery 应用 + beat 周期任务（超时未支付自动关单）
│   ├── tasks.py             # Celery 任务（薄封装 db 层，避免两套实现漂移）
│   └── static/              # 前端页面（原生 HTML+JS，js/api.js 用 fetch 调后端）
├── api/                     # 接口测试
│   ├── api_client.py        # ApiClient：requests.Session + JWT，聚合 6 个 mixin
│   ├── clients/             # 业务接口封装：auth/product/cart/order/store/debug（mixin）
│   ├── common/assert_util.py
│   └── tests/               # 接口用例：auth/cart/order/store/e2e/concurrency
├── ui/                      # UI 测试
│   ├── pages/               # POM：base_page + login/index/cart/orders/seller
│   └── test_ui.py
├── perf/                    # 性能测试
│   ├── locustfile.py        # Locust：login/search/order 三场景（order 分 hot/spread）
│   └── results/             # 压测结果 CSV + 图表
├── config/                  # 多环境配置（config.yaml 默认 + config.docker.yaml）
├── data/                    # yaml 数据驱动（users/search/cart，参数化用例）
├── docs/                    # 文档（database-structure.md）
├── conftest.py              # 全局 fixture（config/base_url/api_client/reset_data/login_user/payment_mode）
├── docker-compose.yml       # 5 服务：db + redis + sut + worker + test
├── Dockerfile               # SUT 镜像（启动时先 init_db 再起 uvicorn）
├── Dockerfile.test          # 测试运行器镜像（含 Playwright Chromium）
├── .github/workflows/ci.yml
├── requirements.txt
├── pytest.ini               # testpaths=api ui；addopts 含 playwright 截图/trace
└── .env / .env.example
```

## 四、核心架构

### 4.1 被测系统（SUT）业务模型

**平台模式**：一个商家可开多店，商品是 SPU（`products` 只有名字），价格/库存下放到店铺维度（`store_product`）。

- 表：`users`（buyer/seller）→ `stores` → `store_product`（价格库存在这）→ `carts` / `orders`（父订单）+ `sub_orders`（子订单，按店铺拆）+ `order_items`（明细）。
- 订单状态机：`pending → paid → shipped → completed`（父订单），子订单同机 + `canceled`。
- 种子账号：`alice`/`bob`（buyer）、`seller1`/`seller2`（seller），密码见 `config/config.yaml`。

### 4.2 测试框架三层

- **接口测试**：`conftest.py` 提供 fixture —— `config`(session，读 yaml+env)、`base_url`(session)、`api_client`(function，独立 Session+token)、`reset_data`(autouse，每测试前清库重播种子)、`login_user`、`payment_mode`。业务接口封装成 `ApiClient` 的 mixin，测试里 `api_client.xxx()` 调用。数据驱动用 `data/*.yaml` + `pytest.mark.parametrize`。
- **UI 测试**：`BasePage` 封装 goto/fill/click/get_text，业务页继承并声明 `URL_PATH` + 元素选择器（优先 id/data-* 属性）。`page` fixture 每测试独立 context。
- **性能测试**：`locustfile.py` 三场景（login/search/order），`SCENARIO`/`ORDER_MODE` 环境变量切换；`ORDER_MODE=hot` 抢同一商品测行锁竞争上限，`spread` 分散测吞吐。

## 五、怎么跑

### 本地（首次需先建库）

```bash
# 0. 首次准备：建库建表 + 插种子（幂等，可重复执行）
.venv/Scripts/python -c "from server.db import init_db; init_db()"

# 1. 终端1 起 SUT —— 必须 DEBUG_MODE=1，否则 /debug/* 故障注入路由不注册，测试 reset 失败全挂
DEBUG_MODE=1 .venv/Scripts/python -m uvicorn server.main:app --reload

# 2. 终端2 跑测试（testpaths=api ui，不含 perf）
.venv/Scripts/python -m pytest

# 3. Allure 报告
.venv/Scripts/python -m pytest --alluredir=reports && allure serve reports
```

### Docker / CI

```bash
docker compose up --build   # db+redis+sut+worker+test 一起起，test 跑完退出
```

CI（`.github/workflows/ci.yml`）：push/PR 到 main/master 触发 → `docker compose build` → `docker compose up --exit-code-from test` → 失败时上传 Playwright 产物。

## 六、关键设计点（面试抓手）

| 设计点 | 实现 | 位置 |
|--------|------|------|
| 防超卖 | 扣库存用**原子条件 UPDATE**（`SET stock=stock-qty WHERE ... AND stock>=qty`），InnoDB 行锁 + `rowcount==0` 判库存不足 + 事务回滚。**不是线程锁**（阶段0旧实现已废弃） | `db.py:create_order` |
| 拆单 | 下单按店铺拆父订单 + 子订单 + 明细，因发货是店铺维度 | `db.py:create_order` |
| JWT + 密码哈希 | 登录发 JWT（HS256/30min），密码 PBKDF2 加盐哈希（10w 次迭代） | `main.py` / `db.py` |
| 支付故障注入 | `PAYMENT_MODE` 切 success/fail/timeout，DEBUG 下 `/debug/payment-mode` 切换 | `main.py` |
| 超时关单幂等 | Celery beat 周期扫 pending 超时订单，取消 + 回补库存；用「条件 UPDATE WHERE status='pending'」抢占实现幂等（重复关单不回补） | `db.py:_cancel_order_in_tx` + `celery_app.py` |
| 越权防护 | 水平（`_check_store_owner` 店铺 owner / `get_order` 订单 username）+ 垂直（`_require_seller` 角色） | `main.py` / `db.py` |
| 连接池 | DBUtils `PooledDB`，每操作独立借还（FastAPI 同步 handler 丢线程池跑，共享连接有线程安全问题） | `db.py:_get_pool` |
| 写偏斜防护 | `confirm_sub_order` 用 `FOR UPDATE` 锁父订单行，串行化并发确认 | `db.py:confirm_sub_order` |

## 七、坑 / 注意事项

1. **`DEBUG_MODE=1` 必须带**：否则 `/debug/reset`、`/debug/payment-mode`、`/debug/cancel-expired` 不注册，`reset_data` autouse fixture 会因 reset 失败导致全部测试挂掉。
2. **端口冲突**：宿主机 3306 被本地 MySQL 占用，`docker-compose.yml` 把容器 MySQL 映射到 `3307:3306`。
3. **压测账号不在种子数据**：`locustfile.py` 用 `perfuser0..999`（密码 `perf123`），压测前需预造这些账号。
4. **敏感信息**：DB 密码、`SECRET_KEY` 在 `.env`（不进 git），`config.yaml` 不再存密钥。

## 八、文档与进度

- 数据库结构参考：`docs/database-structure.md`（表结构 + 实例数据 + 下单数据流）。
- **开发进度** → `D:\秋招追踪\进度追踪.md`；**教学/面试准备进度** → `D:\秋招追踪\shophub-onboarding.md`（Map→Walk→Probe→Master 四级）。

## 九、协作原则

- 关键决策与动手环节留给用户（面试深挖不露馅），我负责搭框架、讲解、模拟面试追问。
- 对话用简体中文；心理健康优先于进度，不催不施压。
