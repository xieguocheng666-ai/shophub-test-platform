# ShopHub 商城测试平台

ShopHub 是个人测试练习项目：围绕模拟商城建立接口、网页和性能测试。被测系统（SUT，System Under Test）是测试所针对的 FastAPI + MySQL 后端，业务包括登录、搜索、加购、下单、模拟支付、按店铺发货和确认收货。

后端与部分测试由 AI 协作完成；仓库内容不证明本人独立编写、运行或掌握全部实现。本项目没有真实支付和企业上线经历。

## 新对话与长期维护入口

接手项目按 [AGENTS](AGENTS.md) → [项目状态](docs/项目状态.md) → [长期路线](docs/长期路线.md) 阅读，再核对实际代码与Git工作区。当前进度、最近验证和未定事项只在项目状态维护；路线图中的候选技术不代表已经采用。

例如，新对话可以这样开始：“继续ShopHub项目，请先读项目AGENTS、项目状态和长期路线，再从状态页的下一步接着讨论。”

## 项目组成

- `server/`：FastAPI + MySQL模拟商城，Celery + Redis支持周期关单。
- `api/`、`unit/`：接口测试与不依赖外部服务的单元回归，使用pytest组织执行。
- `ui/`：Playwright网页测试，页面对象封装页面操作，用例判断结果。
- `perf/`：Locust性能脚本、历史数据及核对报告。
- `docs/`：当前状态、路线、设计、业务参考与验收记录。

模块关系、关键机制和当前结构限制统一见 [架构说明](docs/架构说明.md)。

## 本地运行（Windows PowerShell）

接口和网页测试会在每例前清表并重播种子，只能指向可丢弃的专用测试环境，不与压测或其他测试共享库。使用单个服务进程，确保进程内的模拟支付模式一致。

```powershell
# 项目根目录：创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium

# 仅首次复制，随后在 .env 中填写专用环境的连接信息
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

初始化前先确认目标 MySQL 实例和库只用于本项目测试。`init_db()` 仍硬编码创建 `shop`：下面的初始化命令仅用于专用实例且 `DB_NAME=shop` 的情形。若使用其他库名，必须预建目标库并应用 `server/db.py` 的 DDL（建表语句）和种子，不能以为改 `DB_NAME` 就会自动建新库。11-09验收使用的是另外预建的隔离库。

```powershell
# 终端1：环境信息已确认后初始化；先导入 main 以加载项目 .env
$env:DB_NAME = 'shop'
$env:DEBUG_MODE = '1'
.\.venv\Scripts\python.exe -c "from server import main; main.db.init_db()"
.\.venv\Scripts\python.exe -m uvicorn server.main:app --port 8000
```

```powershell
# 终端2：使用与服务相同的专用数据库配置
$env:DB_NAME = 'shop'
$env:BASE_URL = 'http://127.0.0.1:8000'
.\.venv\Scripts\python.exe -m pytest

# 可选：安装 Allure 命令行工具后查看报告
.\.venv\Scripts\python.exe -m pytest --alluredir=reports
allure serve reports
```

只检查测试清单、不执行用例：`.\.venv\Scripts\python.exe -m pytest --collect-only -q -o addopts=''`。只运行单元回归：`.\.venv\Scripts\python.exe -m pytest unit -q -o addopts=''`。

## Docker 与流水线配置

```powershell
docker compose up --build --abort-on-container-exit --exit-code-from test
```

该命令按 Compose 配置启动5个服务，以 test 的退出码作为结果。Compose 使用本地演示凭据；不能把它当生产部署方案。流水线在推送到 main/master 或收到拉取请求时配置构建、测试和失败产物上传；本文不宣称已验证远程运行。

## 测试数量与证据

默认收集范围为 `unit api ui`。参数化是同一用例按多组输入分别执行，例如数量1与100分别计一条。最近有记录的数量、执行环境、结果和未验证项统一见 [项目状态](docs/项目状态.md#最近验证与证据)，用例依据见 [用例设计](docs/test-cases.md)。

历史通过只证明对应版本和环境；配置存在不能证明容器或远程流水线运行成功。`reports/`和`test-results/`默认不随Git克隆获得，证据摘要与可获得性以阶段验收说明为准。

## 阅读入口

- [统一协作入口](AGENTS.md)：接手顺序、文档分工和维护规则。
- [项目状态](docs/项目状态.md)：当前目标、最近证据、限制和下一步。
- [长期路线](docs/长期路线.md)：阶段方向、依赖与尚未决定的选项。
- [架构说明](docs/架构说明.md)：模块关系、机制和限制。
- [决策记录](docs/决策记录.md)：重要选择及取舍。
- [用例设计与断言](docs/test-cases.md)：业务风险、具体输入、预期和用例对应关系。
- [数据库结构](docs/database-structure.md)：8张表、父子订单和数据变化示例。
- [需求草案](docs/requirements.md)：实现后补写、尚待本人逐条确认，不是企业需求评审记录。
- [质量验收](docs/quality-review-2026-11-09.md)：修复和运行证据、个人参与边界。
- [历史压测核对](perf/report.md)：原始数据、错误原因边界和同负载比较；请求速率与成功订单吞吐分开解释。
- [性能复验方案](PERF_TEST_PLAN.md)：如何保留版本、环境、错误与同负载对照。
